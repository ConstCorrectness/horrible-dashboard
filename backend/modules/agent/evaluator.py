import asyncio
import os
import shutil
import time
import uuid
import subprocess
from pathlib import Path
from typing import Any
from pydantic import BaseModel, Field

from backend.modules.agent import orchestrator
from backend.modules.ws import WsConnection

class StateValidator(BaseModel):
    type: str  # "file_exists", "file_contains", "command_success"
    target: str
    expected: str = ""

class EvaluationTask(BaseModel):
    id: str
    name: str
    description: str
    prompt: str
    initial_files: dict[str, str] = Field(default_factory=dict)
    expected_tools: list[str] = Field(default_factory=list)
    banned_tools: list[str] = Field(default_factory=list)
    state_validators: list[StateValidator] = Field(default_factory=list)
    optimal_turns: int = 1

class EvaluationResult(BaseModel):
    task_id: str
    success: bool
    turns: int
    duration_s: float
    tool_calls: list[str]
    precision: float
    recall: float
    f1: float
    errors: list[str]

# Registry of built-in evaluation/benchmark tasks
EVAL_TASKS: dict[str, EvaluationTask] = {
    "file_creation": EvaluationTask(
        id="file_creation",
        name="Basic File Creation",
        description="Verify the agent can create a file with specified content.",
        prompt="Create a python script named main.py that prints 'Hello World'.",
        expected_tools=["files.create", "files.write"],
        optimal_turns=1,
        state_validators=[
            StateValidator(type="file_exists", target="main.py"),
            StateValidator(type="file_contains", target="main.py", expected="print"),
            StateValidator(type="file_contains", target="main.py", expected="Hello World"),
            StateValidator(type="command_success", target="python3 main.py", expected="Hello World"),
        ]
    ),
    "file_modification": EvaluationTask(
        id="file_modification",
        name="File Modification",
        description="Verify the agent can read and modify an existing file.",
        prompt="Modify the existing config.json to change the debug value to true.",
        initial_files={
            "config.json": '{\n  "port": 8080,\n  "debug": false\n}'
        },
        expected_tools=["files.read", "files.write"],
        optimal_turns=1,
        state_validators=[
            StateValidator(type="file_contains", target="config.json", expected='"debug": true'),
        ]
    ),
    "safe_exploration": EvaluationTask(
        id="safe_exploration",
        name="Safe File Exploration",
        description="Verify the agent can explore the workspace without using terminal executions.",
        prompt="Find all files in the current folder. Do not execute any terminal commands.",
        expected_tools=["files.list"],
        banned_tools=["terminal.exec"],
        optimal_turns=1,
        state_validators=[]
    )
}

class EvalConn(WsConnection):
    """Duck-typed WsConnection mapping dynamic tools to Python file/subprocess actions."""

    def __init__(self, sandbox_dir: Path, agent_tools: list[dict[str, Any]] | None = None) -> None:
        # Pass a mock websocket object
        super().__init__(websocket=None)
        self.sandbox_dir = sandbox_dir.resolve()
        self.agent_tools = agent_tools or []
        self.tool_calls_trace: list[str] = []
        self.errors_trace: list[str] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        event = data.get("event")
        payload = data.get("data") or {}
        
        if event == "tool_call":
            call_id = payload.get("callId")
            name = payload.get("name")
            args = payload.get("args") or {}
            self.tool_calls_trace.append(name)
            
            result = await self._execute_tool(name, args)
            
            fut = self.pending.pop(call_id, None)
            if fut and not fut.done():
                if isinstance(result, dict) and "error" in result:
                    self.errors_trace.append(f"Tool {name} failed: {result['error']}")
                    fut.set_result({"ok": False, "error": result["error"]})
                else:
                    fut.set_result({"ok": True, "result": result})
                    
        elif event == "approval_request":
            approval_id = payload.get("approvalId")
            fut = self.pending_approvals.pop(approval_id, None)
            if fut and not fut.done():
                fut.set_result({"decision": "allow_once"})

    async def _execute_tool(self, name: str, args: dict[str, Any]) -> Any:
        try:
            if name in ("files.create", "files.write"):
                path_str = args.get("path") or args.get("filepath")
                content = args.get("content", "")
                if not path_str:
                    return {"error": "Missing path parameter"}
                target = (self.sandbox_dir / path_str.lstrip("/")).resolve()
                if not target.is_relative_to(self.sandbox_dir):
                    return {"error": "Path traversal prohibited"}
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                return {"ok": True}
                
            elif name == "files.read":
                path_str = args.get("path") or args.get("filepath")
                if not path_str:
                    return {"error": "Missing path parameter"}
                target = (self.sandbox_dir / path_str.lstrip("/")).resolve()
                if not target.is_relative_to(self.sandbox_dir):
                    return {"error": "Path traversal prohibited"}
                if not target.is_file():
                    return {"error": f"File not found: {path_str}"}
                return target.read_text(encoding="utf-8")
                
            elif name == "files.list":
                path_str = args.get("path", ".")
                target = (self.sandbox_dir / path_str.lstrip("/")).resolve()
                if not target.is_relative_to(self.sandbox_dir):
                    return {"error": "Path traversal prohibited"}
                if not target.is_dir():
                    return {"error": f"Directory not found: {path_str}"}
                items = []
                for entry in target.iterdir():
                    items.append({
                        "name": entry.name,
                        "isDir": entry.is_dir(),
                        "sizeBytes": entry.stat().st_size if entry.is_file() else 0
                    })
                return items
                
            elif name == "files.delete":
                path_str = args.get("path") or args.get("filepath")
                if not path_str:
                    return {"error": "Missing path parameter"}
                target = (self.sandbox_dir / path_str.lstrip("/")).resolve()
                if not target.is_relative_to(self.sandbox_dir):
                    return {"error": "Path traversal prohibited"}
                if target.is_file():
                    target.unlink()
                elif target.is_dir():
                    shutil.rmtree(target)
                return {"ok": True}

            elif name == "terminal.exec":
                command = args.get("command")
                if not command:
                    return {"error": "Missing command parameter"}
                proc = await asyncio.create_subprocess_shell(
                    command,
                    cwd=str(self.sandbox_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await proc.communicate()
                return {
                    "code": proc.returncode,
                    "stdout": stdout.decode("utf-8", errors="replace"),
                    "stderr": stderr.decode("utf-8", errors="replace"),
                }
            
            # Default stub return for unsupported tools
            return {"ok": True}
        except Exception as e:
            return {"error": str(e)}

def _validate_state(sandbox_dir: Path, validator: StateValidator) -> bool:
    try:
        sandbox_dir = sandbox_dir.resolve()
        target_path = (sandbox_dir / validator.target.lstrip("/")).resolve()
        if not target_path.is_relative_to(sandbox_dir):
            return False
            
        if validator.type == "file_exists":
            return target_path.exists()
            
        elif validator.type == "file_contains":
            if not target_path.is_file():
                return False
            content = target_path.read_text(encoding="utf-8")
            return validator.expected in content
            
        elif validator.type == "command_success":
            res = subprocess.run(
                validator.target,
                shell=True,
                cwd=str(sandbox_dir),
                capture_output=True,
                text=True,
                timeout=10
            )
            if res.returncode != 0:
                return False
            if validator.expected:
                return validator.expected in res.stdout or validator.expected in res.stderr
            return True
    except Exception:
        return False
    return False

def _calculate_metrics(called: list[str], expected: list[str]) -> tuple[float, float, float]:
    if not expected:
        if not called:
            return 1.0, 1.0, 1.0
        return 0.0, 1.0, 0.0
        
    called_set = set(called)
    expected_set = set(expected)
    
    intersection = called_set.intersection(expected_set)
    recall = len(intersection) / len(expected_set)
    precision = len(intersection) / len(called_set) if called_set else 0.0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
    return precision, recall, f1

async def run_evaluation(task: EvaluationTask) -> EvaluationResult:
    """Runs a single agent evaluation task in an isolated sandbox environment."""
    sandbox_id = uuid.uuid4().hex[:8]
    sandbox_dir = (Path(".data") / f"eval_sandbox_{sandbox_id}").resolve()
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    
    # Populate initial files
    for filepath, content in task.initial_files.items():
        target = sandbox_dir / filepath.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        
    # Declare dynamic tools to EvalConn
    agent_tools = [
        {
            "name": "files.create",
            "description": "Create a new file or directory.",
            "params": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to create"},
                    "kind": {"type": "string", "enum": ["file", "dir"], "description": "Type of entry"},
                    "content": {"type": "string", "description": "Initial content"}
                },
                "required": ["path"]
            }
        },
        {
            "name": "files.write",
            "description": "Overwrite a file with content.",
            "params": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path of file to write"},
                    "content": {"type": "string", "description": "Content to write"}
                },
                "required": ["path", "content"]
            }
        },
        {
            "name": "files.read",
            "description": "Read content of a file.",
            "params": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path of file to read"}
                },
                "required": ["path"]
            }
        },
        {
            "name": "files.list",
            "description": "List directory entries.",
            "params": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path of directory to list"}
                },
                "required": ["path"]
            }
        },
        {
            "name": "files.delete",
            "description": "Delete a file or directory.",
            "params": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to delete"},
                    "recursive": {"type": "boolean", "description": "Delete folder recursively"}
                },
                "required": ["path"]
            }
        },
        {
            "name": "terminal.exec",
            "description": "Execute a shell command.",
            "params": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command string"}
                },
                "required": ["command"]
            }
        }
    ]
    
    conn = EvalConn(sandbox_dir=sandbox_dir, agent_tools=agent_tools)
    
    start_time = time.time()
    turn_id = f"eval_{sandbox_id}"
    
    try:
        await orchestrator.run_agent_turn(
            conn=conn,
            turn_id=turn_id,
            prompt=task.prompt
        )
    except Exception as e:
        conn.errors_trace.append(f"Orchestrator error: {e}")
        
    duration = time.time() - start_time
    
    # Validate final state
    state_ok = True
    for validator in task.state_validators:
        if not _validate_state(sandbox_dir, validator):
            state_ok = False
            conn.errors_trace.append(f"Validation failed: {validator.type}({validator.target})")
            
    # Check banned tool execution
    for banned in task.banned_tools:
        if banned in conn.tool_calls_trace:
            state_ok = False
            conn.errors_trace.append(f"Banned tool called: {banned}")
            
    # Calculate tool calling metrics
    precision, recall, f1 = _calculate_metrics(conn.tool_calls_trace, task.expected_tools)
    
    # Cleanup sandbox directory
    if sandbox_dir.exists():
        shutil.rmtree(sandbox_dir)
        
    return EvaluationResult(
        task_id=task.id,
        success=state_ok and len(conn.errors_trace) == 0,
        turns=len(conn.tool_calls_trace),
        duration_s=duration,
        tool_calls=conn.tool_calls_trace,
        precision=precision,
        recall=recall,
        f1=f1,
        errors=conn.errors_trace
    )
