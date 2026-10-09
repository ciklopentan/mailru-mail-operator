import importlib.util,sys,unittest
from pathlib import Path
from unittest.mock import patch
modpath=Path(__file__).resolve().parents[1]/'backend'/'mailru.py'
spec=importlib.util.spec_from_file_location('mailru',modpath);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class MailTests(unittest.TestCase):
 def test_mime(self):
  from email.message import EmailMessage
  x=EmailMessage();x['From']='Alice <alice@example.org>';x['Subject']='=?utf-8?b?0KLQtdGB0YI=?=';x.set_content('Привет!');x.add_attachment(b'abc',maintype='application',subtype='octet-stream',filename='demo.txt')
  p=m.parsed(x.as_bytes(),'23',r'\\Seen',True)
  self.assertEqual(p['subject'],'Тест');self.assertEqual(p['body'].strip(),'Привет!');self.assertEqual(p['attachments'][0]['size'],3)
 def test_injection_prevented(self):
  with self.assertRaises(m.MailError):m.clean('hello\r\nBcc:bad@example.org')
  with self.assertRaises(m.MailError):m.addr_check(['bad@'])
 def test_send_requires_confirm_before_network(self):
  with self.assertRaisesRegex(m.MailError,'CONFIRM_REQUIRED'):m.op_send({'to':['a@example.com'],'body':'a'})
 def test_move_requires_confirm_before_network(self):
  with self.assertRaisesRegex(m.MailError,'CONFIRM_REQUIRED'):m.op_move({'uid':'1'})
 def test_uid_valid(self):
  self.assertEqual(m.uid_ok('112233'),'112233')
  with self.assertRaises(m.MailError):m.uid_ok('1 STORE 2')
 def test_message_build_with_bcc_private(self):
  x=m.make_message({'to':['a@example.com'],'bcc':['b@example.org'],'subject':'Тема','body':'Тест'},'sender@mail.ru')
  self.assertIsNone(x.get('Bcc'));self.assertEqual(x['To'],'a@example.com');self.assertIn('Тест',x.get_content())
 def test_html_to_text(self):
  self.assertIn('hello',m.html_text('<b>hello</b><script>evil()</script>'));self.assertNotIn('evil',m.html_text('<b>hello</b><script>evil()</script>'))
 def test_cli_status_without_secrets(self):
  with patch.object(m,'CRED',Path('/tmp/non-existent-mailru-credentials-probe')):
   self.assertFalse(m.op_status({})['configured'])
if __name__=='__main__':unittest.main()
