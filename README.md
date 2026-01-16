# Tokacookies Game

This is a simple cookie-clicker game with real-time social features.

## Running the Application

1.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

2.  **Set up the Database:**
    This project uses Supabase for its database. You will need to create a new project on [Supabase](https://supabase.com/) and then run the SQL scripts in `schema.sql` to create the necessary tables and functions. You can do this by copying and pasting the contents of `schema.sql` into the Supabase SQL editor.

3.  **Run the Server:**
    ```bash
    python3 server.py
    ```

## Running the Tests

1.  **Install Test Dependencies:**
    ```bash
    pip install pytest-playwright
    ```

2.  **Run the Tests:**
    ```bash
    python3 -m pytest
    ```
