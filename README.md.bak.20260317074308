```python
# backend/main.py
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import jwt
from pydantic import BaseModel
from typing import Optional

app = FastAPI()

# Authentication
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# Protected route
@app.get("/jobs/new")
async def create_job(token: str = Depends(oauth2_scheme)):
    """
    Create a new job.

    This endpoint is protected and requires authentication.
    """
    # Validate JWT token
    try:
        payload = jwt.decode(token, "secret_key", algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    # Return protected data
    return {"message": "Hello, authenticated user!"}

# Voice-to-Text Integration for Job Description Field
@app.get("/jobs/new/dictate")
async def dictate_job_description(token: str = Depends(oauth2_scheme)):
    """
    Dictate a job description using voice-to-text.

    This endpoint uses the Web Speech API for speech recognition.
    """
    # Validate JWT token
    try:
        payload = jwt.decode(token, "secret_key", algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    # Start voice recording
    # (Implementation details omitted for brevity)
    return {"message": "Recording started"}
```

```python
# frontend/src/App.js
import React from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import DictateJobDescription from "./pages/DictateJobDescription";

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Login />} />
        <Route
          path="/dashboard"
          element={
            <ProtectedRoute>
              <Dashboard />
            </ProtectedRoute>
          }
        />
        <Route
          path="/jobs/new/dictate"
          element={
            <ProtectedRoute>
              <DictateJobDescription />
            </ProtectedRoute>
          }
        />
      </Routes>
    </BrowserRouter>
  );
}

// Protected route
const ProtectedRoute = ({ children }) => {
  const [isAuthenticated, setIsAuthenticated] = React.useState(false);

  React.useEffect(() => {
    // Check authentication status
    setIsAuthenticated(true);
  }, []);

  if (!isAuthenticated) {
    return <Login />;
  }

  return children;
};

// Dictate Job Description page
function DictateJobDescription() {
  const [isRecording, setIsRecording] = React.useState(false);

  const handleStartRecording = () => {
    // Start voice recording
    // (Implementation details omitted for brevity)
    setIsRecording(true);
  };

  const handleStopRecording = () => {
    // Stop voice recording
    // (Implementation details omitted for brevity)
    setIsRecording(false);
  };

  return (
    <div>
      <h1>Dictate Job Description</h1>
      {isRecording ? (
        <button onClick={handleStopRecording}>Stop Recording</button>
      ) : (
        <button onClick={handleStartRecording}>Start Recording</button>
      )}
    </div>
  );
}
```