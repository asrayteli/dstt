from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(100), nullable=True, default='unknown')
    
    def __repr__(self):
        return f'<User {self.username}>'
    
    def get_id(self):
        # usernameを返すように修正（leave_mgr.pyでcurrent_user.usernameを使用しているため）
        return str(self.username)