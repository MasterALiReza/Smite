import re

def fix_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if "import asyncio" not in content and filepath.endswith("tunnels.py"):
        content = content.replace("import time", "import time\nimport asyncio")

    # Add await to start/stop server/forward calls
    content = re.sub(r'(\s+)(request\.app\.state\.[a-z_]+\.start_server\()', r'\1await \2', content)
    content = re.sub(r'(\s+)(request\.app\.state\.[a-z_]+\.stop_server\()', r'\1await \2', content)
    content = re.sub(r'(\s+)(request\.app\.state\.[a-z_]+\.start_forward\()', r'\1await \2', content)
    content = re.sub(r'(\s+)(request\.app\.state\.[a-z_]+\.stop_forward\()', r'\1await \2', content)
    content = re.sub(r'(\s+)(manager\.start_server\()', r'\1await \2', content)
    content = re.sub(r'(\s+)(manager\.stop_server\()', r'\1await \2', content)
    
    # Add await to is_running
    content = re.sub(r'if not request\.app\.state\.([a-z_]+)\.is_running\(', r'if not await request.app.state.\1.is_running(', content)
    content = re.sub(r'if not manager\.is_running\(', r'if not await manager.is_running(', content)
    
    # Replace time.sleep with await asyncio.sleep
    content = re.sub(r'(\s+)time\.sleep\(', r'\1await asyncio.sleep(', content)

    # Specific to main.py
    if "main.py" in filepath:
        content = re.sub(r'(\s+)(gost_forwarder\.(?:cleanup_all|start_forward|stop_forward)\()', r'\1await \2', content)
        content = re.sub(r'(\s+)(rathole_server_manager\.start_server\()', r'\1await \2', content)
        content = re.sub(r'(\s+)(backhaul_manager\.start_server\()', r'\1await \2', content)
        content = re.sub(r'(\s+)(chisel_server_manager\.start_server\()', r'\1await \2', content)
        content = re.sub(r'(\s+)(frp_server_manager\.start_server\()', r'\1await \2', content)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

fix_file(r'c:\Users\iWexort\Documents\Github\Smite-main\panel\app\routers\tunnels.py')
fix_file(r'c:\Users\iWexort\Documents\Github\Smite-main\panel\main.py')
print("Fixes applied successfully.")
