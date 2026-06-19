from app import send_email
import os

mail_user = os.getenv('MAIL_USERNAME')
if not mail_user:
    print('MAIL_USERNAME not set')
else:
    print('Sending test email to', mail_user)
    ok = send_email(mail_user, 'Test Email (runner)', '<p>This is a test from runner</p>', text='Test email')
    print('send_email returned', ok)
