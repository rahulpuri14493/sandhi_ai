```python
from flask import Flask, redirect, url_for, request
from flask_login import LoginManager, login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, FileField
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
    voice_input = FileField('Voice Input', validators=[DataRequired()], id='voice-input')
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

# Define JavaScript code to handle voice input
@app.route('/js/voice-input.js')
def voice_input_js():
    return render_template('voice-input.js')

# Define route to handle voice input submission
@app.route('/submit-voice-input', methods=['POST'])
@login_required
def submit_voice_input():
    # Get voice input from request
    voice_input = request.files['voice-input']
    # Process voice input here
    return redirect(url_for('create_job'))

if __name__ == '__main__':
    app.run(debug=True)
```

And the `create_job.html` template:
```html
{% extends 'base.html' %}

{% block content %}
  <h1>Create Job</h1>
  <form method="post">
    {{ form.hidden_tag() }}
    <div>
      {{ form.job_title.label }}<br>
      {{ form.job_title(size=64) }}
    </div>
    <div>
      {{ form.job_description.label }}<br>
      {{ form.job_description(size=64) }}
    </div>
    <div>
      <input type="file" id="voice-input" name="voice-input">
      <script src="{{ url_for('js/voice-input.js') }}"></script>
    </div>
    <div>
      {{ form.submit() }}
    </div>
  </form>
{% endblock %}
```

And the `voice-input.js` template:
```javascript
// Get the voice input file input element
const voiceInput = document.getElementById('voice-input');

// Add event listener to voice input file input element
voiceInput.addEventListener('change', (e) => {
  // Get the voice input file
  const file = e.target.files[0];

  // Create a new FileReader object
  const reader = new FileReader();

  // Define a callback function to handle the file load event
  reader.onload = (event) => {
    // Get the voice input text
    const voiceInputText = event.target.result;

    // Send a POST request to the server to process the voice input
    fetch('/submit-voice-input', {
      method: 'POST',
      body: new FormData([voiceInput]),
    })
    .then((response) => response.json())
    .then((data) => console.log(data))
    .catch((error) => console.error(error));
  };

  // Read the voice input file as text
  reader.readAsText(file);
});
```
This code adds a voice input file input element to the `create_job.html` template, and a JavaScript file to handle the voice input submission. The JavaScript file uses the `FileReader` API to read the voice input file as text, and sends a POST request to the server to process the voice input. The server-side code handles the voice input submission by processing the voice input file and redirecting the user to the `create_job` route.