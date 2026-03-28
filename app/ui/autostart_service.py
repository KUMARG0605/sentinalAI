#!/usr/bin/env python3
"""
Windows auto-startup configuration for SentinelAI background service
"""
import os
import sys
import winreg
from pathlib import Path

def get_executable_path():
    """Get the path to the executable"""
    if getattr(sys, 'frozen', False):
        # Running as compiled executable
        return sys.executable
    else:
        # Running as script
        script_path = Path(__file__).resolve().parents[2] / "app" / "main_service.py"
        return f'"{sys.executable}" "{script_path}"'

def enable_service_autostart():
    """Enable SentinelAI service to start with Windows"""
    try:
        # Open the registry key for startup programs
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE
        )
        
        # Get the executable path
        exe_path = get_executable_path()
        
        # Set the registry value
        winreg.SetValueEx(
            key,
            "SentinelAI",
            0,
            winreg.REG_SZ,
            exe_path
        )
        
        winreg.CloseKey(key)
        return True
        
    except Exception as e:
        print(f"Failed to enable autostart: {e}")
        return False

def disable_service_autostart():
    """Disable SentinelAI service from starting with Windows"""
    try:
        # Open the registry key for startup programs
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE
        )
        
        # Delete the registry value
        try:
            winreg.DeleteValue(key, "SentinelAI")
        except FileNotFoundError:
            pass  # Value doesn't exist
            
        winreg.CloseKey(key)
        return True
        
    except Exception as e:
        print(f"Failed to disable autostart: {e}")
        return False

def is_autostart_enabled():
    """Check if autostart is enabled"""
    try:
        # Open the registry key for startup programs
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ
        )
        
        # Check if the value exists
        try:
            value, _ = winreg.QueryValueEx(key, "SentinelAI")
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            winreg.CloseKey(key)
            return False
            
    except Exception as e:
        print(f"Failed to check autostart status: {e}")
        return False

def create_startup_shortcut():
    """Create a startup shortcut as alternative method"""
    try:
        import winshell
        from win32com.client import Dispatch
        
        desktop = winshell.desktop()
        path = os.path.join(desktop, "SentinelAI.lnk")
        target = get_executable_path()
        wDir = os.path.dirname(target)
        icon = target
        
        shell = Dispatch('WScript.Shell')
        shortcut = shell.CreateShortCut(path)
        shortcut.Targetpath = target
        shortcut.WorkingDirectory = wDir
        shortcut.IconLocation = icon
        shortcut.save()
        
        return True
        
    except Exception as e:
        print(f"Failed to create shortcut: {e}")
        return False
