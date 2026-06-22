from app import db, app, Task, CompletedTask, User
import uuid
with app.app_context():
    u = User(username=str(uuid.uuid4()), email=str(uuid.uuid4())+'@test.com', password_hash='123', referral_code=str(uuid.uuid4())[:8])
    t = Task(title='test', description='test')
    db.session.add(u)
    db.session.add(t)
    db.session.commit()
    ct = CompletedTask(user_id=u.id, task_id=t.id)
    db.session.add(ct)
    db.session.commit()
    
    db.session.delete(t)
    db.session.commit()
    
    print("CompletedTask remaining:", CompletedTask.query.filter_by(id=ct.id).first() is not None)
