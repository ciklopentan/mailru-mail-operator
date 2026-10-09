#!/usr/bin/env python3
"""Mail.ru IMAP/SMTP mailbox operations using Python standard library.
Run on the owner's trusted VPS via an authenticated remote-execution connector.
No incoming network listener and no third-party package dependency.
"""
from __future__ import annotations
import argparse
import base64
import binascii
import hashlib
import mimetypes
import stat
import uuid
import shutil
import datetime as dt
import email
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import getaddresses, formatdate, make_msgid
import html
import imaplib
import json
import os
from pathlib import Path
import re
import smtplib
import socket
import ssl
import sys
from contextlib import contextmanager
from html.parser import HTMLParser

CRED = Path(os.environ.get('MAILRU_CREDENTIAL_FILE','/etc/mailru-agent/credentials.json'))
ATTACHMENTS = Path(os.environ.get('MAILRU_ATTACHMENT_DIR','/var/lib/mailru-agent/attachments'))
IMAP_HOST = 'imap.mail.ru'
SMTP_HOST = 'smtp.mail.ru'
MAX_RESULT = 100
MAX_SCAN = 1000
MAX_ATTACH = 18_000_000
MAX_ATTACH_TOTAL = 18_000_000
UPLOAD_INBOX = Path(os.environ.get('MAILRU_UPLOAD_INBOX', '/home/myrcdeploy/mailru-upload-inbox'))
CHAT_INBOX = Path(os.environ.get('MAILRU_CHAT_INBOX', '/var/lib/mailru-agent/incoming'))
OUTGOING = ATTACHMENTS / 'outgoing'

class MailError(Exception): pass

class PlainHTML(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]; self.skip=False
    def handle_starttag(self,t,a):
        if t in ('script','style'): self.skip=True
        if t in ('p','div','br','li','tr'): self.parts.append('\n')
    def handle_endtag(self,t):
        if t in ('script','style'): self.skip=False
        if t in ('p','div','li','tr'): self.parts.append('\n')
    def handle_data(self,d):
        if not self.skip: self.parts.append(d)

def html_text(raw):
    p=PlainHTML(); p.feed(raw); return html.unescape(''.join(p.parts)).strip()

def decoded(value):
    try: return str(make_header(decode_header(str(value or ''))))
    except Exception: return str(value or '')

def clean(v,maxlen=500):
    s=str(v or '')
    if any(c in s for c in '\r\n\x00'): raise MailError('Control character in parameter')
    if len(s)>maxlen: raise MailError('Parameter too long')
    return s

def addr_check(values):
    if isinstance(values,str): values=[values]
    if not isinstance(values,list) or not values: raise MailError('Nonempty email address list required')
    out=[]
    for display,address in getaddresses(values):
        if '\n' in address or '\r' in address or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',address):
            raise MailError('Invalid email address')
        out.append(address)
    if not out or len(out)>50: raise MailError('Invalid recipient count')
    return out

def credentials():
    if not CRED.is_file(): raise MailError('NOT_CONFIGURED: app-specific password must be configured locally')
    if os.name != 'nt' and (CRED.stat().st_mode & 0o077): raise MailError('Credential permissions must be 0600')
    data=json.loads(CRED.read_text(encoding='utf-8'))
    if not isinstance(data.get('address'),str) or not isinstance(data.get('app_password'),str) or not data['app_password']:
        raise MailError('Invalid credentials file')
    return data

@contextmanager
def imap():
    c=credentials(); conn=None
    try:
        conn=imaplib.IMAP4_SSL(IMAP_HOST,993,ssl_context=ssl.create_default_context(),timeout=20)
        conn.login(c['address'],c['app_password']); yield conn
    finally:
        if conn is not None:
            try: conn.logout()
            except Exception: pass

def folder_names(c):
    typ,lines=c.list()
    if typ!='OK': raise MailError('LIST failed')
    result=[]
    for raw in lines or []:
        s=raw.decode('utf-8','replace') if isinstance(raw,bytes) else str(raw)
        m=re.match(r'\(([^)]*)\)\s+"?([^" ]+)"?\s+(.+)',s)
        flags=m.group(1).lower() if m else ''
        name=(m.group(3) if m else s.rsplit(' ',1)[-1]).strip().strip('"')
        result.append({'name':name,'flags':flags})
    return result

def resolve_folder(c,folder):
    folders=folder_names(c)
    fld=str(folder or 'INBOX')
    if fld.upper()=='INBOX': return 'INBOX'
    aliases={'trash':'\\trash','drafts':'\\drafts','sent':'\\sent','spam':'\\junk'}
    if fld.lower() in aliases:
        for f in folders:
            if aliases[fld.lower()] in f['flags']: return f['name']
        for f in folders:
            if any(x in f['name'].casefold() for x in {
                'trash':['trash','корзин'], 'drafts':['draft','чернов'],
                'sent':['sent','отправл'], 'spam':['spam','спам']
            }[fld.lower()]): return f['name']
        raise MailError(f'Cannot discover {fld} folder')
    if not any(f['name']==fld for f in folders): raise MailError('Unknown folder')
    return fld

def choose(c,folder,readonly=True):
    name=resolve_folder(c,folder)
    typ,_=c.select('"'+name.replace('"','\\"')+'"',readonly=readonly)
    if typ!='OK': raise MailError(f'Cannot select folder: {name}')
    return name

def uid_ok(uid):
    s=str(uid)
    if not re.fullmatch(r'\d{1,18}',s): raise MailError('Invalid IMAP UID')
    return s

def fetch_bytes(c,uid,headers=False):
    u=uid_ok(uid)
    spec='(BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID)] FLAGS)' if headers else '(BODY.PEEK[] FLAGS)'
    typ,result=c.uid('FETCH',u,spec)
    if typ!='OK' or not result: raise MailError('Message fetch failed')
    data=b''; flags=''
    for item in result:
        if isinstance(item,tuple):
            flags+=item[0].decode('ascii','replace') if isinstance(item[0],bytes) else str(item[0]); data+=item[1] or b''
    if not data: raise MailError('Message not found')
    return data,flags

def parsed(raw,uid,flags='',body=False):
    msg=email.message_from_bytes(raw,policy=policy.default)
    result={'uid':str(uid),'subject':decoded(msg.get('Subject')),'from':decoded(msg.get('From')),
            'to':decoded(msg.get('To')),'date':str(msg.get('Date','')),
            'message_id':str(msg.get('Message-ID','')),'seen':'\\Seen' in flags,
            'attachments':[]}
    if body:
        plain=[]; rich=[]
        for part in msg.walk():
            if part.is_multipart(): continue
            fn=part.get_filename()
            if fn:
                result['attachments'].append({'index':len(result['attachments'])+1,'filename':decoded(fn),
                  'content_type':part.get_content_type(),'size':len(part.get_payload(decode=True) or b'')})
                continue
            if part.get_content_disposition()=='attachment': continue
            try: payload=part.get_content()
            except Exception: payload=(part.get_payload(decode=True) or b'').decode('utf-8','replace')
            if part.get_content_type()=='text/plain': plain.append(str(payload))
            elif part.get_content_type()=='text/html': rich.append(html_text(str(payload)))
        result['body']=('\n'.join(plain) or '\n'.join(rich))[:100000]
        result['body_truncated']=len('\n'.join(plain) or '\n'.join(rich))>100000
        result['reply_to']=str(msg.get('Reply-To') or msg.get('From',''))
        result['references']=str(msg.get('References',''))
    return result

def op_folders(a):
    with imap() as c: return {'folders':folder_names(c)}

def op_list(a):
    n=max(1,min(int(a.get('limit',20)),MAX_RESULT))
    with imap() as c:
        folder=choose(c,a.get('folder','INBOX'))
        criteria='UNSEEN' if a.get('unread_only') else 'ALL'
        typ,data=c.uid('SEARCH',None,criteria)
        if typ!='OK': raise MailError('IMAP search failed')
        ids=(data[0] or b'').split(); ids=ids[-n:][::-1]
        rows=[]
        for u in ids:
            try: raw,flags=fetch_bytes(c,u.decode(),True); rows.append(parsed(raw,u.decode(),flags))
            except MailError: pass
        return {'folder':folder,'messages':rows,'count':len(rows),'total_matching':len((data[0] or b'').split())}

def op_search(a):
    query=str(a.get('query','')).casefold().strip(); sender=str(a.get('from','')).casefold().strip()
    if not (query or sender or a.get('unread_only')): raise MailError('Specify query, from, or unread_only')
    scan=max(1,min(int(a.get('scan_limit',300)),MAX_SCAN))
    limit=max(1,min(int(a.get('limit',30)),MAX_RESULT))
    with imap() as c:
        folder=choose(c,a.get('folder','INBOX'))
        typ,data=c.uid('SEARCH',None,'UNSEEN' if a.get('unread_only') else 'ALL')
        if typ!='OK':raise MailError('IMAP search failed')
        candidates=(data[0] or b'').split()[-scan:][::-1]; rows=[]
        for u in candidates:
            try:
                raw,flags=fetch_bytes(c,u.decode(),False if a.get('in_body') else True)
                m=parsed(raw,u.decode(),flags,body=bool(a.get('in_body')))
                if sender and sender not in m['from'].casefold(): continue
                if query and not any(query in str(m.get(k,'')).casefold() for k in ('subject','from','to','body')): continue
                if not a.get('in_body'): m.pop('body',None)
                rows.append(m)
                if len(rows)>=limit:break
            except MailError:pass
        return {'folder':folder,'messages':rows,'scanned':len(candidates),'scan_limited':len((data[0] or b'').split())>scan}

def op_read(a):
    with imap() as c:
        fld=choose(c,a.get('folder','INBOX')); raw,flags=fetch_bytes(c,a['uid'])
        return {'folder':fld,'message':parsed(raw,a['uid'],flags,True)}

def op_mark(a):
    seen=bool(a.get('seen',True))
    with imap() as c:
        fld=choose(c,a.get('folder','INBOX'),False)
        typ,_=c.uid('STORE',uid_ok(a['uid']),'+FLAGS.SILENT' if seen else '-FLAGS.SILENT','(\\Seen)')
        if typ!='OK': raise MailError('Unable to change Seen flag')
        return {'folder':fld,'uid':uid_ok(a['uid']),'seen':seen}

def op_move(a):
    if not a.get('confirm'):raise MailError('CONFIRM_REQUIRED for moving a message')
    with imap() as c:
        src=choose(c,a.get('folder','INBOX'),False); dst=resolve_folder(c,a.get('destination','trash'))
        if src==dst: raise MailError('Source and destination are identical')
        uid=uid_ok(a['uid']); typ,_=c.uid('MOVE',uid,'"'+dst.replace('"','\\"')+'"')
        if typ=='OK': return {'uid':uid,'from':src,'to':dst,'moved':True,'method':'UID MOVE'}
        raise MailError('Server does not support safe atomic UID MOVE; message not changed')

def _leaf_name(value):
    name=str(value or '')
    if (not name or name in ('.','..') or len(name)>180 or
        name != name.strip() or '/' in name or '\\' in name or
        any(ord(c)<32 or ord(c)==127 for c in name)):
        raise MailError('Invalid attachment filename')
    return name

def _source_under(source, roots, required_suffix):
    """Accept only plain files in prearranged inboxes; do not follow symlinks."""
    p=Path(str(source))
    if not p.is_absolute() or p.suffix != required_suffix:
        raise MailError('Invalid staged source path')
    if not any(p.parent == root for root in roots):
        raise MailError('Source is outside approved incoming directory')
    if not re.fullmatch(r'[a-f0-9]{32}'+re.escape(required_suffix),p.name):
        raise MailError('Invalid temporary source name')
    fd=os.open(p,os.O_RDONLY | getattr(os,'O_NOFOLLOW',0) | getattr(os,'O_NONBLOCK',0))
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise MailError('Source is not a regular unlinked-safe file')
        if st.st_size <= 0 or st.st_size > (MAX_ATTACH*4//3+8192 if required_suffix=='.b64' else MAX_ATTACH):
            raise MailError('File must be 1–18 MB')
        return fd,st.st_size
    except Exception:
        os.close(fd)
        raise

def _stage_bytes(source, filename, expected_sha=None, encoded=False):
    name=_leaf_name(filename)
    roots=(CHAT_INBOX,) if encoded else (UPLOAD_INBOX,CHAT_INBOX)
    fd,rawsize=_source_under(source,roots,'.b64' if encoded else '.data')
    ident=uuid.uuid4().hex
    ATTACHMENTS.mkdir(mode=0o700,parents=True,exist_ok=True)
    OUTGOING.mkdir(mode=0o700,exist_ok=True)
    directory=OUTGOING/ident
    directory.mkdir(mode=0o700,exist_ok=False)
    dest=directory/'content'
    content=b''
    try:
        with os.fdopen(fd,'rb') as fp:
            data=fp.read()
        if encoded:
            try:content=base64.b64decode(data,validate=True)
            except binascii.Error:raise MailError('Invalid base64 staged file')
        else:content=data
        if not 0<len(content)<=MAX_ATTACH:raise MailError('Attachment must be 1–18 MB')
        digest=hashlib.sha256(content).hexdigest()
        if expected_sha and str(expected_sha).lower()!=digest:raise MailError('SHA256 mismatch, nothing staged')
        f=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(f,'wb') as out:out.write(content)
        meta={'id':ident,'filename':name,'size':len(content),'sha256':digest}
        f=os.open(directory/'info.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(f,'w',encoding='utf-8') as out:json.dump(meta,out,ensure_ascii=False)
        # Only delete our disposable inbox file, never arbitrary originals.
        Path(source).unlink()
        return meta
    except Exception:
        shutil.rmtree(directory,ignore_errors=True)
        raise

def op_stage(a):
    if not a.get('source') or not a.get('name'):raise MailError('stage needs source and name')
    return {'staged':_stage_bytes(a['source'],a['name'],a.get('sha256'),False)}

def op_stage_base64(a):
    if not a.get('source') or not a.get('name'):raise MailError('stage_base64 needs source and name')
    return {'staged':_stage_bytes(a['source'],a['name'],a.get('sha256'),True)}

def _staged_meta(ident):
    if not isinstance(ident,str) or not re.fullmatch(r'[a-f0-9]{32}',ident):
        raise MailError('Invalid attachment ID')
    directory=OUTGOING/ident
    info=directory/'info.json'
    data=directory/'content'
    if info.is_symlink() or data.is_symlink() or directory.is_symlink():
        raise MailError('Attachment symlink not allowed')
    try:
        meta=json.loads(info.read_text(encoding='utf-8'))
        if meta['id']!=ident or not 0<int(meta['size'])<=MAX_ATTACH:raise MailError('Invalid staging metadata')
        _leaf_name(meta['filename'])
        if data.stat().st_size!=meta['size']:raise MailError('Staged attachment size changed')
        if hashlib.sha256(data.read_bytes()).hexdigest()!=meta['sha256']:
            raise MailError('Staged attachment checksum changed')
        return meta,data
    except (OSError,ValueError,KeyError,TypeError):raise MailError('Staged attachment is missing or damaged')

def op_staged(a):
    records=[]
    if OUTGOING.is_dir():
        for direntry in sorted(OUTGOING.iterdir()):
            try:
                meta,_=_staged_meta(direntry.name)
                records.append(meta)
            except MailError:pass
    return {'attachments':records,'count':len(records)}

def op_unstage(a):
    if a.get('confirm') is not True:raise MailError('CONFIRM_REQUIRED for removing a staged file')
    ident=str(a.get('id',''))
    _staged_meta(ident)
    shutil.rmtree(OUTGOING/ident)
    return {'removed':True,'id':ident}

def _prepared_attachments(items):
    if not isinstance(items,list):raise MailError('Attachments must be an array')
    if len(items)>10:raise MailError('Max 10 attachments')
    result=[];total=0
    for item in items:
        if isinstance(item,str) and re.fullmatch(r'[a-f0-9]{32}',item):
            meta,path=_staged_meta(item)
            name=meta['filename']
        else:
            # Backward compatibility with legacy files already kept under ATTACHMENTS.
            if not isinstance(item,str) or not item or '\x00' in item:raise MailError('Invalid legacy attachment')
            path=(ATTACHMENTS/item).resolve()
            root=ATTACHMENTS.resolve()
            if root not in path.parents or not path.is_file() or path.is_symlink():
                raise MailError('Attachment must be a file under approved directory')
            name=_leaf_name(path.name)
        size=path.stat().st_size
        if size<=0 or size>MAX_ATTACH:raise MailError('Attachment exceeds 18 MB')
        total+=size
        if total>MAX_ATTACH_TOTAL:raise MailError('Total attachment payload exceeds 18 MB')
        result.append((path,name,size))
    return result

def make_message(a,sender):
    m=EmailMessage(); m['From']=sender
    for kind in ('to','cc','bcc'):
        if a.get(kind):
            dest=addr_check(a[kind])
            if kind!='bcc':m[kind.title()]=', '.join(dest)
    if not a.get('to'):raise MailError('To is required')
    m['Subject']=clean(a.get('subject',''),1000)
    m['Date']=formatdate(localtime=False);m['Message-ID']=make_msgid(domain=sender.split('@',1)[1])
    if a.get('in_reply_to'):
        val=clean(a['in_reply_to'],1000)
        m['In-Reply-To']=val
    if a.get('references'): m['References']=clean(a['references'],3000)
    m.set_content(str(a.get('body','')))
    if a.get('html'):m.add_alternative(str(a['html']),subtype='html')
    for path,filename,size in _prepared_attachments(a.get('attachments',[])):
        mimetype,_=mimetypes.guess_type(filename)
        major,minor=(mimetype or 'application/octet-stream').split('/',1)
        m.add_attachment(path.read_bytes(),maintype=major,subtype=minor,filename=filename)
    return m

def op_draft(a):
    cfg=credentials(); msg=make_message(a,cfg['address'])
    with imap() as c:
        draft=resolve_folder(c,'drafts')
        typ,_=c.append('"'+draft.replace('"','\\"')+'"', '\\Draft', imaplib.Time2Internaldate(dt.datetime.now(dt.timezone.utc)),msg.as_bytes())
        if typ!='OK':raise MailError('Draft APPEND failed')
    return {'draft_saved':True,'folder':draft,'subject':a.get('subject','')}

def op_send(a):
    if a.get('confirm') is not True: raise MailError('CONFIRM_REQUIRED: send requires true')
    cfg=credentials(); msg=make_message(a,cfg['address'])
    rcpts=addr_check(a['to'])
    for k in ('cc','bcc'):
        if a.get(k):rcpts+=addr_check(a[k])
    with smtplib.SMTP_SSL(SMTP_HOST,465,context=ssl.create_default_context(),timeout=25) as smtp:
        smtp.login(cfg['address'],cfg['app_password'])
        refused=smtp.sendmail(cfg['address'],rcpts,msg.as_bytes())
    if refused: raise MailError('SMTP reported some recipients were rejected')
    return {'accepted_by_smtp':True,'message_id':msg['Message-ID'],'recipient_count':len(rcpts),'note':'SMTP acceptance does not prove final delivery'}

def op_attachment(a):
    with imap() as c:
        fld=choose(c,a.get('folder','INBOX')); raw,flags=fetch_bytes(c,a['uid'])
    msg=email.message_from_bytes(raw,policy=policy.default)
    ix=int(a['index']); items=[p for p in msg.walk() if not p.is_multipart() and p.get_filename()]
    if not (1<=ix<=len(items)):raise MailError('Attachment index out of bounds')
    part=items[ix-1]; data=part.get_payload(decode=True) or b''
    if len(data)>MAX_ATTACH:raise MailError('Attachment over 20 MB')
    filename=re.sub(r'[^A-Za-z0-9._-]','_',decoded(part.get_filename()))[:120]
    if filename in ('','.','..'):filename='attachment'
    ATTACHMENTS.mkdir(parents=True,exist_ok=True,mode=0o700)
    p=ATTACHMENTS/(f"{uid_ok(a['uid'])}_{ix}_"+filename)
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:f.write(data)
    return {'folder':fld,'file':str(p),'bytes':len(data)}

def op_status(a):
    return {'configured':CRED.is_file(),'credentials_path':str(CRED),'imap_host':IMAP_HOST,'smtp_host':SMTP_HOST,
            'available':['folders','list','search','read','mark','move','draft','send','attachment','stage','stage_base64','staged','unstage','status']}

OPS={'status':op_status,'folders':op_folders,'list':op_list,'search':op_search,'read':op_read,'mark':op_mark,
     'move':op_move,'draft':op_draft,'send':op_send,'attachment':op_attachment,
     'stage':op_stage,'stage_base64':op_stage_base64,'staged':op_staged,'unstage':op_unstage}

def main(argv=None):
    p=argparse.ArgumentParser(description='Owner-only Mail.ru mailbox CLI')
    p.add_argument('action',choices=OPS);p.add_argument('--payload-file',type=Path)
    p.add_argument('--folder',default=None);p.add_argument('--uid',default=None);p.add_argument('--limit',type=int,default=None)
    p.add_argument('--query',default=None);p.add_argument('--unread-only',action='store_true');p.add_argument('--confirm',action='store_true')
    opts=vars(p.parse_args(argv)); action=opts.pop('action'); payload=opts.pop('payload_file')
    try:
        arg=json.loads(payload.read_text(encoding='utf-8')) if payload else {}
        if not isinstance(arg,dict): raise MailError('Payload must be a JSON object')
        for k,v in opts.items():
            if v is not None and (v is not False or k in ('unread_only','confirm') and v):arg[k]=v
        res=OPS[action](arg)
        print(json.dumps({'ok':True,'action':action,'result':res},ensure_ascii=False))
        return 0
    except Exception as e:
        # No traceback: SMTP/auth exceptions can include server payloads. Never reveal configuration or password.
        if isinstance(e,MailError): reason=str(e)
        elif isinstance(e,(imaplib.IMAP4.error,smtplib.SMTPException)):reason='Authentication, provider rejection or protocol error (details suppressed)'
        elif isinstance(e,(socket.timeout,TimeoutError,OSError)):reason='Network, file or TLS error: '+type(e).__name__
        else:reason='Unexpected error: '+type(e).__name__
        print(json.dumps({'ok':False,'action':action,'error':reason},ensure_ascii=False))
        return 1
if __name__=='__main__':sys.exit(main())
