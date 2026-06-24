"""
AOE4 自动生产村民工具 - PyInstaller 打包脚本

使用方法：
    python build.py              # 打包旧版GUI CPU精简版（复用已有虚拟环境）
    python build.py --full       # 打包旧版GUI完整版（含torch GPU支持，约2GB）
    python build.py --cli        # 打包旧版命令行版本
    python build.py --editor     # 打包【节点编辑器（引擎, v3.0）】：含网页前端 + pywebview
    python build.py --clean      # 清理包括虚拟环境（强制重新下载依赖）
    python build.py --help       # 显示帮助

原理说明：
    CPU精简版：自动创建临时虚拟环境，安装 CPU-only 版本的 PyTorch 后打包，
    确保打包出的 exe 不包含 CUDA 库，体积大幅缩小。
    虚拟环境默认保留以便复用，加 --clean 才删除。

依赖安装：
    pip install pyinstaller

打包完成后，exe文件在 build/aoe4_gui_cpu/ 目录中
"""
import os
import sys
import shutil
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "build", "aoe4_gui_cpu")
VENV_DIR = os.path.join(BASE_DIR, "build", "_build_venv")


def clean_output():
    """清理输出目录和临时文件"""
    def _safe_rmtree(path):
        """安全删除目录，忽略权限错误"""
        if not os.path.exists(path):
            return
        try:
            shutil.rmtree(path)
            print(f"  已清理: {path}")
        except PermissionError:
            print(f"  跳过（文件被占用）: {path}")

    _safe_rmtree(OUTPUT_DIR)
    # 保留虚拟环境以便复用，用 --clean 参数才删除
    if getattr(clean_output, '_clean_venv', False):
        _safe_rmtree(VENV_DIR)
    elif os.path.exists(VENV_DIR):
        print(f"  保留虚拟环境（复用）: {VENV_DIR}")

    # 清理 PyInstaller 临时构建文件
    build_dir = os.path.join(BASE_DIR, "build")
    if os.path.exists(build_dir):
        for name in os.listdir(build_dir):
            if name.endswith('_build'):
                _safe_rmtree(os.path.join(build_dir, name))

    # 清理 .spec 文件
    for f in os.listdir(BASE_DIR):
        if f.endswith('.spec'):
            try:
                os.remove(os.path.join(BASE_DIR, f))
                print(f"  已清理: {f}")
            except PermissionError:
                pass

    # 清理 hooks 目录
    _safe_rmtree(os.path.join(BASE_DIR, "build", "_hooks"))


def _create_runtime_hook():
    """创建运行时钩子"""
    hook_dir = os.path.join(BASE_DIR, "build", "_hooks")
    os.makedirs(hook_dir, exist_ok=True)

    hook_content = '''\
# Runtime hook: 设置环境变量
import os
os.environ['CUDA_VISIBLE_DEVICES'] = ''
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'
'''
    hook_path = os.path.join(hook_dir, "runtime_hook.py")
    with open(hook_path, 'w', encoding='utf-8') as f:
        f.write(hook_content)

    return hook_path


def _setup_cpu_venv():
    """
    创建临时虚拟环境并安装 CPU-only 版本的依赖

    返回虚拟环境的 python 路径和 pip 路径
    """
    if os.path.exists(VENV_DIR):
        venv_python = os.path.join(VENV_DIR, "Scripts", "python.exe")
        if os.path.exists(venv_python):
            print(f"  复用已有虚拟环境: {VENV_DIR}")
            return venv_python

    print("\n  创建临时虚拟环境（CPU-only PyTorch）...")

    # 创建虚拟环境
    subprocess.run(
        [sys.executable, "-m", "venv", VENV_DIR],
        check=True,
    )
    print("  虚拟环境已创建")

    venv_python = os.path.join(VENV_DIR, "Scripts", "python.exe")
    venv_pip = os.path.join(VENV_DIR, "Scripts", "pip.exe")

    # 升级 pip
    subprocess.run(
        [venv_python, "-m", "pip", "install", "--upgrade", "pip"],
        check=True,
        capture_output=True,
    )

    # 读取 requirements.txt 并替换 torch 为 CPU 版本
    req_file = os.path.join(BASE_DIR, "requirements.txt")
    with open(req_file, 'r', encoding='utf-8') as f:
        requirements = f.read()

    # 安装依赖，torch 使用 CPU 版本
    print("  安装 CPU-only PyTorch...")

    # 先安装 torch CPU 版本（从 PyTorch 官方源）
    torch_cpu_url = "https://download.pytorch.org/whl/cpu"
    subprocess.run(
        [venv_pip, "install",
         "torch", "torchvision",
         "--index-url", torch_cpu_url],
        check=True,
    )

    # 安装其余依赖（排除 torch/torchvision，已安装 CPU 版）
    skip_packages = {'torch', 'torchvision', 'torchaudio', 'pyinstaller'}
    install_list = []
    for line in requirements.strip().split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        line = line.split('#', 1)[0].strip()   # 去掉行内注释（如 "pywebview  # 说明"），否则整行喂给 pip 会报错
        if not line:
            continue
        pkg_name = line.split('==')[0].split('>=')[0].split('<=')[0].split('[')[0].strip().lower()
        if pkg_name not in skip_packages:
            install_list.append(line)

    if install_list:
        print(f"  安装其他依赖: {', '.join(install_list)}")
        subprocess.run(
            [venv_pip, "install"] + install_list,
            check=True,
        )

    # 安装 pyinstaller
    subprocess.run(
        [venv_pip, "install", "pyinstaller"],
        check=True,
        capture_output=True,
    )

    # 验证 torch 是 CPU 版本
    result = subprocess.run(
        [venv_python, "-c",
         "import torch; print(f'torch {torch.__version__}'); "
         "print(f'CUDA: {torch.cuda.is_available()}')"],
        capture_output=True, text=True,
    )
    print(f"  {result.stdout.strip()}")

    print("  虚拟环境准备完成")
    return venv_python


def _build_with_venv(venv_python, entry_script, exe_name, output_dir, console_mode,
                     extra_datas=None, extra_hiddenimports=None, collect_pkgs=None, icon_path=None):
    """使用虚拟环境的 Python 执行 PyInstaller 打包。

    extra_datas: 追加的 datas 条目(spec 字符串，如网页前端目录)；
    extra_hiddenimports: 追加的隐藏导入(如 webview/clr)；
    collect_pkgs: 用 collect_all 兜底收集的包名(如 webview/pythonnet，静态分析抓不全)；
    icon_path: exe 图标(.ico 或 .png；PyInstaller 配合 Pillow 会把 PNG 自动转 .ico)。"""

    pi_build_dir = os.path.join(BASE_DIR, "build", f"{exe_name}_build")
    runtime_hook_path = _create_runtime_hook()

    # 获取 easyocr 模型目录（从虚拟环境中获取）
    easyocr_model_dir = None
    result = subprocess.run(
        [venv_python, "-c",
         "import easyocr, os; print(os.path.join(os.path.dirname(easyocr.__file__), 'model'))"],
        capture_output=True, text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        model_path = result.stdout.strip()
        if os.path.exists(model_path):
            easyocr_model_dir = model_path

    # 构建 datas
    datas_entries = [
        f"(os.path.join(base_dir, 'templates'), 'templates')",
    ]
    if easyocr_model_dir:
        datas_entries.append(f"(r'{easyocr_model_dir}', 'easyocr/model')")
    datas_entries += list(extra_datas or [])

    datas_str = ',\n        '.join(datas_entries)

    # hiddenimports：基础 + 调用方追加（如引擎需要 webview/clr）
    hidden = ['easyocr', 'torch', 'torchvision', 'cv2', 'numpy', 'PIL',
              'mss', 'pydirectinput', 'psutil'] + list(extra_hiddenimports or [])
    hidden_str = ',\n        '.join(repr(h) for h in hidden)

    # 对 pywebview/pythonnet 这类“静态分析抓不全”的包，用 collect_all 兜底收集其数据/二进制/子模块。
    # 始终先定义三个空列表，使下面 Analysis 的引用恒有效（无 collect_pkgs 时即空）。
    collect_prelude = "_collect_datas, _collect_bins, _collect_hidden = [], [], []\n"
    if collect_pkgs:
        collect_prelude += (
            "from PyInstaller.utils.hooks import collect_all\n"
            f"for _pkg in {list(collect_pkgs)!r}:\n"
            "    try:\n"
            "        _d, _b, _h = collect_all(_pkg)\n"
            "        _collect_datas += _d; _collect_bins += _b; _collect_hidden += _h\n"
            "    except Exception as _e:\n"
            "        print('collect_all 跳过', _pkg, _e)\n"
        )

    icon_repr = repr(icon_path) if icon_path else 'None'

    spec_content = f'''# -*- mode: python ; coding: utf-8 -*-
import os

block_cipher = None

base_dir = r'{BASE_DIR}'

{collect_prelude}
a = Analysis(
    [os.path.join(base_dir, '{entry_script}')],
    pathex=[base_dir],
    binaries=[*_collect_bins],
    datas=[
        {datas_str},
        *_collect_datas
    ],
    hiddenimports=[
        {hidden_str},
        *_collect_hidden
    ],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[r'{runtime_hook_path}'],
    excludes=[
        # 排除不需要的大型包
        'tensorflow', 'keras',
        'matplotlib', 'pandas', 'notebook',
        'IPython', 'jupyter', 'tensorboard',
        'sklearn', 'scikit-learn',
        'nose', 'pytest', 'sphinx',
        'tornado', 'zmq',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='{exe_name}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console={console_mode},
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon={icon_repr},
    uac_admin=True,
)
'''

    spec_path = os.path.join(BASE_DIR, f"{exe_name}.spec")
    with open(spec_path, 'w', encoding='utf-8') as f:
        f.write(spec_content)

    cmd = [
        venv_python, '-m', 'PyInstaller',
        '--clean',
        '--distpath', output_dir,
        '--workpath', pi_build_dir,
        spec_path,
    ]
    print(f"\n  执行打包: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=BASE_DIR)

    # 清理临时文件
    if os.path.exists(pi_build_dir):
        shutil.rmtree(pi_build_dir)
    if os.path.exists(spec_path):
        os.remove(spec_path)

    if result.returncode == 0:
        exe_path = os.path.join(output_dir, f'{exe_name}.exe')
        if os.path.exists(exe_path):
            size_mb = os.path.getsize(exe_path) / (1024 * 1024)
            print(f"\n  打包成功!")
            print(f"  输出: {exe_path}")
            print(f"  大小: {size_mb:.1f} MB")
        else:
            print(f"\n  打包完成但未找到exe文件，请检查: {output_dir}")
    else:
        print("\n  打包失败!")
        sys.exit(1)


def build_gui_cpu():
    """打包 GUI CPU精简版（使用CPU-only PyTorch）"""
    print("\n" + "=" * 60)
    print("  打包 GUI CPU精简版")
    print("  （使用临时虚拟环境安装 CPU-only PyTorch，排除CUDA库）")
    print("=" * 60)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 创建虚拟环境并安装 CPU-only 依赖
    venv_python = _setup_cpu_venv()

    # 使用虚拟环境打包
    _build_with_venv(
        venv_python=venv_python,
        entry_script="gui_app.py",
        exe_name="AOE4-AutoVillager",
        output_dir=OUTPUT_DIR,
        console_mode=False,
    )


def build_gui_full():
    """打包 GUI 完整版（含torch GPU支持，约2GB）"""
    print("\n" + "=" * 60)
    print("  打包 GUI 完整版（含GPU支持）")
    print("=" * 60)

    full_output_dir = os.path.join(BASE_DIR, "build", "aoe4_gui_full")
    os.makedirs(full_output_dir, exist_ok=True)
    pi_build_dir = os.path.join(BASE_DIR, "build", "aoe4_gui_full_build")
    runtime_hook_path = _create_runtime_hook()

    easyocr_model_dir = None
    try:
        import easyocr
        model_path = os.path.join(os.path.dirname(easyocr.__file__), 'model')
        if os.path.exists(model_path):
            easyocr_model_dir = model_path
    except ImportError:
        pass

    datas_entries = [
        f"(os.path.join(base_dir, 'templates'), 'templates')",
    ]
    if easyocr_model_dir:
        datas_entries.append(f"(r'{easyocr_model_dir}', 'easyocr/model')")
    datas_str = ',\n        '.join(datas_entries)

    spec_content = f'''# -*- mode: python ; coding: utf-8 -*-
import os

block_cipher = None

base_dir = r'{BASE_DIR}'

a = Analysis(
    [os.path.join(base_dir, 'gui_app.py')],
    pathex=[base_dir],
    binaries=[],
    datas=[
        {datas_str}
    ],
    hiddenimports=[
        'easyocr', 'torch', 'torchvision',
        'cv2', 'numpy', 'PIL', 'mss', 'pydirectinput', 'psutil',
    ],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[r'{runtime_hook_path}'],
    excludes=[
        'matplotlib', 'scipy', 'pandas', 'notebook',
        'IPython', 'jupyter', 'tensorboard',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='AOE4-AutoVillager-Full',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
    uac_admin=True,
)
'''

    spec_path = os.path.join(BASE_DIR, "aoe4_gui_full.spec")
    with open(spec_path, 'w', encoding='utf-8') as f:
        f.write(spec_content)

    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--clean',
        '--distpath', full_output_dir,
        '--workpath', pi_build_dir,
        spec_path,
    ]
    print(f"  执行: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=BASE_DIR)

    if os.path.exists(pi_build_dir):
        shutil.rmtree(pi_build_dir)
    if os.path.exists(spec_path):
        os.remove(spec_path)

    if result.returncode == 0:
        exe_path = os.path.join(full_output_dir, 'AOE4-AutoVillager-Full.exe')
        if os.path.exists(exe_path):
            size_mb = os.path.getsize(exe_path) / (1024 * 1024)
            print(f"\n  GUI 完整版打包成功!")
            print(f"  输出: {exe_path}")
            print(f"  大小: {size_mb:.1f} MB")
    else:
        print("\n  GUI 完整版打包失败!")
        sys.exit(1)


def build_cli():
    """打包命令行版本（CPU精简版）"""
    print("\n" + "=" * 60)
    print("  打包命令行版本（CPU精简版）")
    print("=" * 60)

    cli_output_dir = os.path.join(BASE_DIR, "build", "aoe4_cli_cpu")
    os.makedirs(cli_output_dir, exist_ok=True)

    venv_python = _setup_cpu_venv()

    _build_with_venv(
        venv_python=venv_python,
        entry_script="main.py",
        exe_name="AOE4-AutoVillager-CLI",
        output_dir=cli_output_dir,
        console_mode=True,
    )


def build_editor():
    """打包【节点编辑器（引擎, v3.0）】：run_editor.py + 网页前端资源 + pywebview。

    与旧版的关键差异：① datas 追加 flow/editor/web（编辑器 HTML/JS/vendored litegraph，WEB_DIR 按
    webhost.py 相对定位，故目标须还原成 flow/editor/web）与 flows（内置流程）；② 追加 webview/clr 隐藏导入，
    并 collect_all('webview','pythonnet') 兜底（pywebview+pythonnet 静态分析抓不全）。引擎也用到 OCR(easyocr)，
    故同样走 CPU 精简 venv。"""
    print("\n" + "=" * 60)
    print("  打包 节点编辑器（引擎, CPU精简版）")
    print("=" * 60)

    editor_output_dir = os.path.join(BASE_DIR, "build", "aoe4_editor_cpu")
    os.makedirs(editor_output_dir, exist_ok=True)

    venv_python = _setup_cpu_venv()

    _build_with_venv(
        venv_python=venv_python,
        entry_script="run_editor.py",
        exe_name="AOE4-FlowEditor",
        output_dir=editor_output_dir,
        console_mode=False,   # 纯窗口（pywebview 自带窗口，不需要控制台）。需排错时临时改 True 看报错。
        extra_datas=[
            "(os.path.join(base_dir, 'flow', 'editor', 'web'), os.path.join('flow', 'editor', 'web'))",
            "(os.path.join(base_dir, 'flows'), 'flows')",
        ],
        extra_hiddenimports=[
            'webview', 'webview.platforms.winforms', 'clr', 'proxy_tools', 'bottle',
        ],
        collect_pkgs=['webview', 'pythonnet'],
        icon_path=os.path.join(BASE_DIR, "templates", "cunmin.png"),   # 用村民图标做 exe 图标(PNG→ico 由 PyInstaller+Pillow 转)
    )


def main():
    # 检查 PyInstaller
    try:
        import PyInstaller
        print(f"PyInstaller 版本: {PyInstaller.__version__}")
    except ImportError:
        print("错误: 未安装 PyInstaller")
        print("请运行: pip install pyinstaller")
        sys.exit(1)

    # 解析参数
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        sys.exit(0)

    # --clean 参数：清理包括虚拟环境
    if "--clean" in args:
        clean_output._clean_venv = True
        args = [a for a in args if a != "--clean"]

    # 清理旧输出
    print("\n清理旧输出文件...")
    clean_output()

    # 执行打包
    if "--full" in args:
        build_gui_full()
    elif "--cli" in args:
        build_cli()
    elif "--editor" in args:
        build_editor()
    else:
        build_gui_cpu()

    print("\n" + "=" * 60)
    print("  打包完成!")
    print(f"  输出目录: {OUTPUT_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
