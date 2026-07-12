import requests

url = "http://85.133.243.49:8000"
session = requests.Session()
response = session.post(f"{url}/api/auth/login", json={"username": "Wexort", "password": "wexort123"})
if response.status_code == 200:
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    t_resp = session.get(f"{url}/api/core-health/health", headers=headers)
    print("Health status:", t_resp.status_code)
    try:
        print("Health:", t_resp.json())
    except:
        print("Text:", t_resp.text)
else:
    print("Login failed:", response.text)
