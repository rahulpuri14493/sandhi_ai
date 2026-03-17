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
    Protected route to create a new job.

    Requires authentication.
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

# Authentication middleware
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jose import jwt
from pydantic import BaseModel
from typing import Optional

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

async def get_token(current_user: dict = Depends(oauth2_scheme)):
    """
    Get the authentication token from the request.

    :param current_user: The authenticated user.
    :return: The authentication token.
    """
    return current_user["token"]

# Protected route with authentication check
@app.get("/jobs/new")
async def create_job(token: str = Depends(get_token)):
    """
    Protected route to create a new job.

    Requires authentication.
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
```

```python
# frontend/src/App.js
import React from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";

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
```

```python
# frontend/src/pages/Dashboard.js
import React from "react";
import { useNavigate } from "react-router-dom";

function Dashboard() {
  const navigate = useNavigate();

  React.useEffect(() => {
    // Check authentication status
    if (!isAuthenticated) {
      navigate("/login");
    }
  }, []);

  return (
    <div>
      <h1>Dashboard</h1>
      <p>Welcome, authenticated user!</p>
    </div>
  );
}
```

```python
# frontend/src/pages/Login.js
import React from "react";
import { useNavigate } from "react-router-dom";

function Login() {
  const navigate = useNavigate();

  const handleLogin = () => {
    // Handle login logic
    navigate("/dashboard");
  };

  return (
    <div>
      <h1>Login</h1>
      <button onClick={handleLogin}>Login</button>
    </div>
  );
}
```