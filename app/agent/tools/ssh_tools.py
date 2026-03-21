from contextlib import contextmanager

import paramiko
import os

SSH_KEY_PATH = os.getenv("SSH_KEY_PATH", "/path/to/id_rsa")
SSH_USER = "sre_user"

@contextmanager
def get_ssh_client(hostname: str):
    """SSH 连接上下文管理器"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname, username=SSH_USER, key_filename=SSH_KEY_PATH, timeout=10)
        yield client
    except Exception as e:
        raise ConnectionError(f"SSH Connect Failed: {e}")
    finally:
        client.close()

def run_ssh_cmd(hostname: str, command: str):
    """执行 SSH 命令并返回结果"""
    with get_ssh_client(hostname) as client:
        stdin, stdout, stderr = client.exec_command(command)
        exit_status = stdout.channel.recv_exit_status()
        out = stdout.read().decode('utf-8', errors='ignore').strip()
        err = stderr.read().decode('utf-8', errors='ignore').strip()

        if exit_status != 0:
            return f"[System] Command finished with non-zero exit code ({exit_status}).\nstderr: {err}"
        return out
