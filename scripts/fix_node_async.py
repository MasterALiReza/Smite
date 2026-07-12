import re
from pathlib import Path

def migrate_node_core_adapters(filepath):
    path = Path(filepath)
    content = path.read_text(encoding='utf-8')
    
    # Add import asyncio if not present
    if 'import asyncio' not in content:
        content = content.replace('import subprocess', 'import subprocess\nimport asyncio')

    # Convert def apply to async def apply
    content = re.sub(r'def apply\(self, tunnel_id: str, spec: Dict\[str, Any\]\)( -> None)?:', 
                     r'async def apply(self, tunnel_id: str, spec: Dict[str, Any])\1:', content)
    
    # Convert def remove to async def remove
    content = re.sub(r'def remove\(self, tunnel_id: str\)( -> None)?:',
                     r'async def remove(self, tunnel_id: str)\1:', content)
                     
    # Convert proc = subprocess.Popen(cmd, ...) to proc = await asyncio.create_subprocess_exec(*cmd, ...)
    # where cmd is a list like ["/usr/local/bin/rathole", ...] or cmd
    # We will use regex to find subprocess.Popen( and replace it with await asyncio.create_subprocess_exec(*
    # We need to handle the first argument which could be a list literal or a variable.
    
    def repl_popen(m):
        arg = m.group(1)
        if arg.startswith('['):
            return f"proc = await asyncio.create_subprocess_exec(*{arg},"
        else:
            return f"proc = await asyncio.create_subprocess_exec(*{arg},"
            
    content = re.sub(r'proc = subprocess\.Popen\(\s*(\[[^\]]+\]|[a-zA-Z0-9_]+),', repl_popen, content)
    
    # time.sleep -> await asyncio.sleep
    content = re.sub(r'time\.sleep\(([\d.]+)\)', r'await asyncio.sleep(\1)', content)
    
    # proc.poll() -> proc.returncode
    content = re.sub(r'proc\.poll\(\) is not None', r'proc.returncode is not None', content)
    content = re.sub(r'proc\.poll\(\) is None', r'proc.returncode is None', content)
    
    # proc.stderr.read().decode() -> (await proc.stderr.read()).decode()
    content = re.sub(r'proc\.stderr\.read\(\)\.decode\(\)', r'(await proc.stderr.read()).decode()', content)
    
    # proc.wait(timeout=5) -> await asyncio.wait_for(proc.wait(), timeout=5)
    content = re.sub(r'proc\.wait\(timeout=(\d+)\)', r'await asyncio.wait_for(proc.wait(), timeout=\1)', content)
    
    # proc.wait() -> await proc.wait()
    content = re.sub(r'proc\.wait\(\)', r'await proc.wait()', content)
    
    # subprocess.run(["pkill", ...]) -> wait, this is inside def remove, which is now async.
    # We can use await asyncio.create_subprocess_exec(*["pkill", ...]) and wait.
    # Or just keep subprocess.run as it's blocking but very fast (pkill). 
    # To be fully async and consistent, let's leave it or change it? 
    # Let's change it: subprocess.run(...) -> proc_kill = await asyncio.create_subprocess_exec(...); await proc_kill.wait()
    def repl_run(m):
        cmd = m.group(1)
        return f"kill_proc = await asyncio.create_subprocess_exec(*{cmd}, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)\n            await kill_proc.wait()"
        
    content = re.sub(r'subprocess\.run\((\["pkill"[^\]]+\])[^\)]+\)', repl_run, content)

    # In AdapterManager, apply_tunnel and remove_tunnel:
    # adapter.apply(tunnel_id, spec) -> await adapter.apply(tunnel_id, spec)
    content = re.sub(r'adapter\.apply\(tunnel_id, spec\)', r'await adapter.apply(tunnel_id, spec)', content)
    
    # self.active_tunnels[tunnel_id].remove(tunnel_id) -> await self.active_tunnels[tunnel_id].remove(tunnel_id)
    content = re.sub(r'self\.active_tunnels\[tunnel_id\]\.remove\(tunnel_id\)', r'await self.active_tunnels[tunnel_id].remove(tunnel_id)', content)
    
    # adapter.remove(tunnel_id) -> await adapter.remove(tunnel_id)
    content = re.sub(r'adapter\.remove\(tunnel_id\)', r'await adapter.remove(tunnel_id)', content)
    
    # update main.py for node as well to await restore
    # Wait, node/main.py already does `await adapter_manager.restore_tunnels()`, we just need to make sure AdapterManager.restore_tunnels is updated.
    
    # restore_tunnels uses apply_tunnel:
    # `await self.apply_tunnel(...)` inside `restore_tunnels`.
    # Wait, `apply_tunnel` is already async and awaited in `restore_tunnels`?
    # Let's check:
    # async def restore_tunnels(self):
    #     ...
    #     await self.apply_tunnel(tunnel_id, tunnel_core, spec)
    # This is already correct!

    path.write_text(content, encoding='utf-8')
    print("Migration complete for core_adapters.py!")

if __name__ == '__main__':
    migrate_node_core_adapters('node/app/core_adapters.py')
