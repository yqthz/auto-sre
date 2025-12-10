import hashlib

# 计算文件的 MD5 哈希值
def md5_of_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, 'rb') as f:
        # 每次读取 8 kb
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()
