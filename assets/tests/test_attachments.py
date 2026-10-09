import base64
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import mailru as m

class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.incoming=self.root/'pc'
        self.chat=self.root/'chat'
        self.out=self.root/'attachments'
        for x in (self.incoming,self.chat,self.out):x.mkdir()
        for name,value in [('ATTACHMENTS',self.out),('OUTGOING',self.out/'outgoing'),('UPLOAD_INBOX',self.incoming),('CHAT_INBOX',self.chat)]:
            p=patch.object(m,name,value)
            p.start();self.addCleanup(p.stop)
    def _create(self,location,content,suffix):
        p=location/(('f'*32)+suffix)
        p.write_bytes(content)
        return p
    def test_stage_binary_unicode_filename_and_mime(self):
        content=b'\x00\xff\x12PDF\n'+bytes(range(64))
        src=self._create(self.incoming,content,'.data')
        staged=m.op_stage({'source':str(src),'name':'Акт проверки.pdf','sha256':hashlib.sha256(content).hexdigest()})['staged']
        self.assertFalse(src.exists())
        self.assertEqual(staged['filename'],'Акт проверки.pdf')
        self.assertEqual(staged['size'],len(content))
        self.assertEqual(m.op_staged({})['count'],1)
        mail=m.make_message({'to':['receiver@example.org'],'subject':'Проверка','body':'Приложение','attachments':[staged['id']]},'user@mail.ru')
        parts=list(mail.iter_attachments())
        self.assertEqual(len(parts),1)
        self.assertEqual(parts[0].get_filename(),'Акт проверки.pdf')
        self.assertEqual(parts[0].get_content_type(),'application/pdf')
        self.assertEqual(parts[0].get_payload(decode=True),content)
    def test_base64_chat_bytes(self):
        data=b'\x89PNG\r\n\x1a\n'+bytes(range(256))*3
        encoded=base64.b64encode(data)
        src=self._create(self.chat,encoded,'.b64')
        obj=m.op_stage_base64({'source':str(src),'name':'photo.png','sha256':hashlib.sha256(data).hexdigest()})['staged']
        self.assertFalse(src.exists())
        part=list(m.make_message({'to':['me@example.org'],'attachments':[obj['id']]},'user@mail.ru').iter_attachments())[0]
        self.assertEqual(part.get_payload(decode=True),data)
        self.assertEqual(part.get_content_type(),'image/png')
    def test_rejected_bad_checksum_keeps_source(self):
        src=self._create(self.incoming,b'hello','.data')
        with self.assertRaisesRegex(m.MailError,'SHA256 mismatch'):
            m.op_stage({'source':str(src),'name':'hello.txt','sha256':'0'*64})
        self.assertTrue(src.exists())
        self.assertEqual(m.op_staged({})['count'],0)
    def test_reject_invalid_base64(self):
        src=self._create(self.chat,b'?!ab??','.b64')
        with self.assertRaisesRegex(m.MailError,'Invalid base64'):
            m.op_stage_base64({'source':str(src),'name':'bad.txt'})
    def test_reject_traversal_and_symlink(self):
        src=self._create(self.incoming,b'x','.data')
        with self.assertRaisesRegex(m.MailError,'filename'):
            m.op_stage({'source':str(src),'name':'../passwd'})
        outside=self.root/('f'*32+'.data');outside.write_bytes(b'a')
        with self.assertRaisesRegex(m.MailError,'outside'):
            m.op_stage({'source':str(outside),'name':'no.txt'})
        symlink=self.chat/('a'*32+'.data');symlink.symlink_to(src)
        with self.assertRaises(OSError):m.op_stage({'source':str(symlink),'name':'no.txt'})
    def test_tampering_caught(self):
        src=self._create(self.incoming,b'expected','.data')
        obj=m.op_stage({'source':str(src),'name':'text.txt'})['staged']
        (self.out/'outgoing'/obj['id']/'content').write_bytes(b'corrupted')
        with self.assertRaisesRegex(m.MailError,'changed'):
            m.make_message({'to':['me@example.org'],'attachments':[obj['id']]},'me@mail.ru')
    def test_unstage_requires_confirmation(self):
        src=self._create(self.incoming,b'abc','.data')
        obj=m.op_stage({'source':str(src),'name':'doc.txt'})['staged']
        with self.assertRaisesRegex(m.MailError,'CONFIRM_REQUIRED'):m.op_unstage({'id':obj['id']})
        self.assertTrue(m.op_unstage({'id':obj['id'],'confirm':True})['removed'])
        self.assertEqual(m.op_staged({})['count'],0)
    def test_total_size_and_count_rejected(self):
        with self.assertRaisesRegex(m.MailError,'Max 10'):
            m.make_message({'to':['me@example.org'],'attachments':['x']*11},'me@mail.ru')
        with patch.object(m,'MAX_ATTACH_TOTAL',3):
            src=self._create(self.incoming,b'hello','.data')
            obj=m.op_stage({'source':str(src),'name':'text.txt'})['staged']
            with self.assertRaisesRegex(m.MailError,'Total attachment'):
                m.make_message({'to':['me@example.org'],'attachments':[obj['id']]},'me@mail.ru')
    def test_legacy_filename_works(self):
        legacy=self.out/'old-file.csv';legacy.write_bytes(b'a,b\n1,2\n')
        mail=m.make_message({'to':['me@example.org'],'attachments':['old-file.csv']},'me@mail.ru')
        part=list(mail.iter_attachments())[0]
        self.assertEqual(part.get_filename(),'old-file.csv')
        self.assertEqual(part.get_content_type(),'text/csv')

if __name__=='__main__':unittest.main()
