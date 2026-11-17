"""Keep sources available for numba's JIT cache in a frozen application."""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files('librosa', excludes=['**/__pycache__'])
hiddenimports = collect_submodules('librosa')
module_collection_mode = 'py'
