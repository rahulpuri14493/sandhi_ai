```python
# Import necessary libraries
from flask import Flask, redirect, url_for, request
from flask_login import LoginManager, UserMixin, login_required, login_user, logout_user
from flask_session import Session

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret_key'
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_TYPE'] = 'filesystem'

# Initialize session and login manager
Session(app)
login_manager = LoginManager(app)

# Define user model
class User(UserMixin):
    def __init__(self, id, username, password):
        self.id = id
        self.username = username
        self.password = password

# Load user from database
@login_manager.user_loader
def load_user(user_id):
    # Replace with actual database query
    return User(user_id, 'username', 'password')

# Define routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        # Replace with actual authentication logic
        user = User(1, username, password)
        login_user(user)
        return redirect(url_for('create_job'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/jobs/new', methods=['GET', 'POST'])
@login_required
def create_job():
    if request.method == 'POST':
        # Handle form submission
        return 'Job created successfully'
    return render_template('create_job.html')

# Run the application
if __name__ == '__main__':
    app.run(debug=True)
```

Note: The above code is a simplified example to demonstrate the fix. You should replace the `load_user` function with actual database query to load the user from the database. Also, the `login` function should be replaced with actual authentication logic.