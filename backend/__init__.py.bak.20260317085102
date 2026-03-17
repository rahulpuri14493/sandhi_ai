```python
from flask import Flask, redirect, url_for, request
from flask_login import LoginManager, login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret_key'

# Initialize Login Manager
login_manager = LoginManager()
login_manager.init_app(app)

# Define User model (assuming it's defined elsewhere)
from models import User

# Define Create Job Form
class CreateJobForm(FlaskForm):
    job_title = StringField('Job Title', validators=[DataRequired()])
    job_description = StringField('Job Description', validators=[DataRequired()])
    submit = SubmitField('Create Job')

# Define route for Create Job page
@app.route('/jobs/new', methods=['GET', 'POST'])
@login_required  # Add login_required decorator to protect the route
def create_job():
    form = CreateJobForm()
    if form.validate_on_submit():
        # Create job logic here
        return redirect(url_for('jobs'))
    return render_template('create_job.html', form=form)

# Define route for login page
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('jobs'))
    # Login logic here
    return render_template('login.html')

# Define route for logout page
@app.route('/logout')
@login_required
def logout():
    # Logout logic here
    return redirect(url_for('login'))

# Define route for protected dashboard page
@app.route('/dashboard')
@login_required
def dashboard():
    # Dashboard logic here
    return render_template('dashboard.html')

if __name__ == '__main__':
    app.run(debug=True)
```

Note: The above code assumes that you have a User model defined elsewhere in your application. You'll need to replace the `User` import with the actual import statement for your User model. Additionally, you'll need to implement the login and logout logic in the `login` and `logout` functions, respectively.