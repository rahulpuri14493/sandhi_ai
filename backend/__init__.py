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
    # Integrate AI Icon for Job Description Grammar Correction & Prompt Recreation
    # AI icon button adjacent to job description text area
    # Tooltip: “Enhance with AI”
    # Clicking the icon triggers AI processing and shows a loading spinner
    # Results appear in a preview modal with:
    # - Original description (left side).
    # - AI-enhanced description (right side).
    # Include Apply and Cancel buttons for user confirmation
    # AI agent should:
    # - Fix grammar, spelling, and sentence structure.
    # - Improve clarity and professionalism.
    # - Reframe vague prompts into actionable instructions.
    # Original text remains unchanged until user confirms
    # Must work seamlessly with existing document upload functionality
    # Secure API calls with authentication
    # Handle edge cases:
    # - Empty description
    # - Very short text
    # - Non-English input
    # Test with short, medium, and long job descriptions
    # Test with grammatically incorrect text, vague prompts, and multilingual input
    # Verify AI suggestions improve clarity without losing meaning
    # Confirm canceling preserves the original description
    # Ensure accessibility (keyboard navigation, screen reader compatibility)
    # Endpoint should return:
    # - corrected_text → grammar-fixed version.
    # - recreated_prompts → refined instructions.
    # AI icon
    ai_icon = '<button class="btn btn-primary" type="button" data-toggle="tooltip" data-placement="top" title="Enhance with AI"><i class="fas fa-robot"></i></button>'
    return render_template('create_job.html', form=form, ai_icon=ai_icon)

if __name__ == '__main__':
    app.run(debug=True)
```

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
      {{ ai_icon }}
      <input type="file" id="voice-input" name="voice-input">
      <script src="{{ url_for('js/voice-input.js') }}"></script>
    </div>
    <div>
      {{ form.submit() }}
    </div>
  </form>
  <!-- AI-enhanced description preview modal -->
  <div class="modal fade" id="ai-enhanced-description-modal" tabindex="-1" role="dialog" aria-labelledby="ai-enhanced-description-modal-label" aria-hidden="true">
    <div class="modal-dialog" role="document">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title" id="ai-enhanced-description-modal-label">AI-enhanced Description</h5>
          <button type="button" class="close" data-dismiss="modal" aria-label="Close">
            <span aria-hidden="true">&times;</span>
          </button>
        </div>
        <div class="modal-body">
          <div class="row">
            <div class="col-md-6">
              <h4>Original Description</h4>
              <p>{{ form.job_description.value }}</p>
            </div>
            <div class="col-md-6">
              <h4>AI-enhanced Description</h4>
              <p id="ai-enhanced-description"></p>
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-dismiss="modal">Cancel</button>
          <button type="button" class="btn btn-primary" id="apply-ai-enhanced-description">Apply</button>
        </div>
      </div>
    </div>
  </div>
{% endblock %}
```

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
    .then((data) => {
      // Display AI-enhanced description in modal
      const aiEnhancedDescriptionModal = document.getElementById('ai-enhanced-description-modal');
      const aiEnhancedDescription = document.getElementById('ai-enhanced-description');
      aiEnhancedDescriptionModal.style.display = 'block';
      aiEnhancedDescription.innerText = data.corrected_text;
    })
    .catch((error) => console.error(error));
  };

  // Read the voice input file as text
  reader.readAsText(file);
});
```

```python
# Define route to handle AI-enhanced description preview
@app.route('/ai-enhanced-description', methods=['POST'])
@login_required
def ai_enhanced_description():
    # Get AI-enhanced description from request
    ai_enhanced_description = request.form['ai-enhanced-description']
    # Return AI-enhanced description
    return jsonify({'corrected_text': ai_enhanced_description})
```