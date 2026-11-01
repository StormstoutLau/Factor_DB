"""
打包元数据测试

校验 LICENSE、[project.scripts] 入口与命令目标可导入。
"""

import importlib
import unittest
from pathlib import Path


class TestPackaging(unittest.TestCase):
    """打包与整洁项"""

    ROOT = Path(__file__).resolve().parents[1]

    def test_license_file_exists(self):
        """根目录 LICENSE 存在且为 MIT"""
        text = (self.ROOT / 'LICENSE').read_text(encoding='utf-8')
        self.assertIn('MIT License', text)

    def test_pyproject_declares_script_and_license(self):
        """pyproject 以 PEP 639 形式声明 license 与 factor-db 脚本入口"""
        try:
            import tomllib
        except ModuleNotFoundError:
            self.skipTest('tomllib 需要 Python 3.11+')

        data = tomllib.loads((self.ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
        self.assertEqual(data['project']['license'], 'MIT')
        self.assertEqual(data['project']['license-files'], ['LICENSE'])
        self.assertEqual(
            data['project']['scripts']['factor-db'], 'Factor_DB.cli:main'
        )

    def test_build_backend_supports_pep639(self):
        """构建后端版本满足 PEP 639（setuptools>=77）"""
        try:
            import tomllib
        except ModuleNotFoundError:
            self.skipTest('tomllib 需要 Python 3.11+')

        data = tomllib.loads((self.ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
        requires = data['build-system']['requires']
        versions = [req.split('>=')[1] for req in requires if req.startswith('setuptools>=77')]
        self.assertTrue(versions, f'build-system.requires 应含 setuptools>=77: {requires}')

    def test_entry_point_target_callable(self):
        """脚本入口指向的函数可导入且可调用"""
        module_path, _, attr = 'Factor_DB.cli:main'.partition(':')
        module = importlib.import_module(module_path)
        self.assertTrue(callable(getattr(module, attr)))


if __name__ == '__main__':
    unittest.main()
