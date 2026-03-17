```python
# Import required libraries
from flask import Flask, redirect, url_for, request
from flask_login import LoginManager, UserMixin, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy

# Initialize Flask application
app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret_key_here'  # Replace with a secure secret key
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
db = SQLAlchemy(app)

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)

# Define User model
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password = db.Column(db.String(128), nullable=False)

# Define routes
@app.route('/')
def index():
    return 'Welcome to the application!'

@app.route('/jobs/new', methods=['GET', 'POST'])
@login_required  # Protect the route with login_required decorator
def create_job():
    if request.method == 'POST':
        # Handle form submission
        pass
    return render_template('create_job.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.password == password:
            login_user(user)
            return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

# Define login_manager callback
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

if __name__ == '__main__':
    app.run(debug=True)
```

Note: This code snippet is a simplified example and may not cover all the requirements of a real-world application. It's essential to implement proper authentication and authorization mechanisms, such as token-based authentication or OAuth, to ensure the security of your application.