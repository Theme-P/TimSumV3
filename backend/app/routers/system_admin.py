from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any
import psutil
import subprocess
import os

from app.core.auth import get_current_admin
from app.models.user import UserData

router = APIRouter()

@router.get("/resources")
def get_system_resources(current_user: UserData = Depends(get_current_admin)) -> Dict[str, Any]:
    """
    Get current system resource usage: CPU, RAM, Disk.
    Access restricted to Admin/SuperAdmin.
    """
    try:
        # CPU
        cpu_percent = psutil.cpu_percent(interval=0.5)
        
        # Memory
        mem = psutil.virtual_memory()
        ram_percent = mem.percent
        ram_used = round(mem.used / (1024**3), 2)
        ram_total = round(mem.total / (1024**3), 2)
        
        # Disk
        disk = psutil.disk_usage('/')
        disk_percent = disk.percent
        disk_used = round(disk.used / (1024**3), 2)
        disk_total = round(disk.total / (1024**3), 2)
        
        import datetime
        return {
            "cpu_percent": cpu_percent,
            "memory": {
                "percent": ram_percent,
                "used_gb": ram_used,
                "total_gb": ram_total
            },
            "disk": {
                "percent": disk_percent,
                "used_gb": disk_used,
                "total_gb": disk_total
            },
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get system resources: {str(e)}")

@router.post("/ollama/restart")
def restart_ollama(current_user: UserData = Depends(get_current_admin)) -> Dict[str, Any]:
    """
    Restart the Ollama docker container.
    Access restricted to Admin/SuperAdmin.
    """
    try:
        # Assuming the ollama container is named "ollama" or similar in docker-compose
        # For security, we just run docker restart
        result = subprocess.run(
            ["docker", "restart", "ollama"],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode != 0:
            raise HTTPException(
                status_code=500, 
                detail=f"Failed to restart Ollama. Error: {result.stderr}"
            )
            
        return {
            "status": "success",
            "message": "Ollama container restarted successfully"
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Docker restart command timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to restart Ollama: {str(e)}")
