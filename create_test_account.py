import os
from app import app, db, User, Task, CompletedTask, WithdrawalRequest
from werkzeug.security import generate_password_hash
import uuid

with app.app_context():
    # Check if test user exists
    test_user = User.query.filter_by(username='test').first()
    if not test_user:
        test_user = User(
            username='test',
            email='test@example.com',
            password_hash=generate_password_hash('test1234'),
            balance=150.0,
            ccp_account='123456789',
            referral_code=str(uuid.uuid4())[:8]
        )
        db.session.add(test_user)
        db.session.commit()
        print("Created test user.")
    
    # Create some dummy tasks and completed tasks to show in the UI if needed
    if Task.query.count() < 5:
        for i in range(5):
            task = Task(title=f"مهمة تجريبية {i+1}", description="وصف المهمة", link="https://example.com")
            db.session.add(task)
        db.session.commit()

    # Create a pending withdrawal request for test user
    pending_req = WithdrawalRequest.query.filter_by(user_id=test_user.id, status='pending').first()
    if not pending_req:
        req = WithdrawalRequest(user_id=test_user.id, amount=50.0, ccp_account='123456789')
        db.session.add(req)
        db.session.commit()
        print("Created pending withdrawal request for test user.")
        
    print("Test data setup complete.")
