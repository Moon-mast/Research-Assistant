"""交互式厂商选择向导：python -m harness.select

等价于 python -m harness.providers，命名更直观。选中厂商后只需填密钥即可。
"""
from .providers import run_wizard

if __name__ == "__main__":
    run_wizard()
