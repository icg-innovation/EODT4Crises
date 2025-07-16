# backend/Dockerfile

# 1. Use an official Python runtime as a parent image
# Using a slim version keeps the image size smaller
FROM python:3.11-slim

# 2. Set the working directory inside the container
WORKDIR /app

# 3. Copy the requirements file into the container
COPY requirements.txt .

# 4. Install any needed packages specified in requirements.txt
# --no-cache-dir reduces image size
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copy the rest of the backend application code into the container
COPY . .

# 6. Expose the port the app runs on
EXPOSE 4000

# 7. Define the command to run the application using Gunicorn
# Gunicorn is a production-ready web server for Python applications.
CMD ["gunicorn", "--bind", "0.0.0.0:4000", "app:app"]