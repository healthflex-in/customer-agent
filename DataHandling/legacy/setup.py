import os
import sys
import platform
import subprocess
import time


def check_ffmpeg():
    """Check if ffmpeg is installed and accessible"""
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        print("✅ ffmpeg is installed and accessible")
        return True
    except Exception:
        print("❌ ffmpeg is not installed or not in PATH")
        return False


def install_ffmpeg_instructions():
    """Provide instructions for installing ffmpeg based on OS"""
    system = platform.system()

    print("\n=== FFmpeg Installation Instructions ===")

    if system == "Windows":
        print("1. Download ffmpeg from https://ffmpeg.org/download.html")
        print("2. Extract the zip file to a location like C:\\ffmpeg")
        print(
            "3. Add the bin folder (e.g., C:\\ffmpeg\\bin) to your PATH environment variable:"
        )
        print(
            "   a. Search for 'Edit the system environment variables' in the Start menu"
        )
        print("   b. Click 'Environment Variables'")
        print(
            "   c. Under 'System variables', find and select 'Path', then click 'Edit'"
        )
        print("   d. Click 'New' and add the path to the bin folder")
        print("   e. Click 'OK' on all dialogs to save the changes")
        print("4. Restart your terminal/command prompt")

    elif system == "Darwin":  # macOS
        print("Install with Homebrew (recommended):")
        print("1. If you don't have Homebrew, install it first:")
        print(
            '   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
        )
        print("2. Then install ffmpeg:")
        print("   brew install ffmpeg")

    elif system == "Linux":
        print("For Debian/Ubuntu:")
        print("   sudo apt update && sudo apt install ffmpeg")
        print("\nFor Fedora:")
        print("   sudo dnf install ffmpeg")
        print("\nFor CentOS/RHEL:")
        print("   sudo yum install epel-release")
        print("   sudo yum install ffmpeg")

    else:
        print(
            "Please visit https://ffmpeg.org/download.html for installation instructions for your system"
        )

    print(
        "\nAfter installing, restart your terminal/command prompt and run this script again to verify the installation."
    )


def install_python_dependencies():
    """Install required Python packages"""
    print("\n=== Installing Python Dependencies ===")

    requirements_file = "requirements.txt"
    if not os.path.exists(requirements_file):
        print(f"❌ Error: {requirements_file} not found")
        return False

    try:
        print(f"Installing packages from {requirements_file}...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-r", requirements_file]
        )
        print("✅ Python dependencies installed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Error installing dependencies: {e}")
        return False


def main():
    """Main setup function"""
    print("=== Medical Interview Assistant Setup ===\n")

    # Check Python version
    python_version = platform.python_version()
    print(f"Python version: {python_version}")

    major, minor, _ = map(int, python_version.split("."))
    if major < 3 or (major == 3 and minor < 8):
        print(
            "❌ Warning: This application is recommended to run on Python 3.8 or higher"
        )
    else:
        print("✅ Python version is compatible")

    # Install Python dependencies
    py_deps_ok = install_python_dependencies()

    # Check for ffmpeg
    ffmpeg_ok = check_ffmpeg()
    if not ffmpeg_ok:
        install_ffmpeg_instructions()

    # Summary
    print("\n=== Setup Summary ===")
    print(
        "Python Dependencies: "
        + ("✅ Installed" if py_deps_ok else "❌ Installation failed")
    )
    print(
        "FFmpeg: "
        + ("✅ Installed" if ffmpeg_ok else "❌ Not found (see instructions above)")
    )

    if py_deps_ok:
        print("\nYou can now run the application with:")
        print("python main.py")

        if not ffmpeg_ok:
            print(
                "\nNote: The application will attempt to run without ffmpeg, but audio functionality may be limited."
            )

    print("\nPress Enter to exit...")
    input()


if __name__ == "__main__":
    main()
