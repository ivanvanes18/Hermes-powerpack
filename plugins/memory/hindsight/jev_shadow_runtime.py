from __future__ import annotations
import hashlib, json, os, re, stat, time, urllib.error, urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

_FORBIDDEN_KEYS={"user_text","assistant_text","state","request","response","content","prompt"}
_SECRET_RE=re.compile(r"(?i)(sk[_-]|jv_live_|bearer\s|private key|password\s*=)")
@dataclass(frozen=True)
class PilotSnapshot:
    pilot_id:str; target:int; valid_evaluations:int; event_count:int; terminal_state:str
class ShadowEventStore:
    def __init__(self,root:Path,pilot_id:str,target:int=100):
        if target!=100: raise ValueError("target_invalid")
        self.root=Path(root); self.pilot_id=pilot_id; self.target=target
        if self.root.exists():
            if self.root.is_symlink() or not self.root.is_dir() or stat.S_IMODE(self.root.stat().st_mode)&0o077: raise ValueError("root_unsafe")
        else: self.root.mkdir(parents=True,mode=0o700)
        os.chmod(self.root,0o700); self.events_path=self.root/"events.jsonl"; self.state_path=self.root/"state.json"
        for path in (self.events_path,self.root/".lock"):
            fd=os.open(path,os.O_CREAT|os.O_APPEND|os.O_WRONLY|getattr(os,"O_NOFOLLOW",0),0o600); os.fchmod(fd,0o600); os.close(fd)
        if not self.state_path.exists(): self._write_state(0,0)
        elif stat.S_IMODE(self.state_path.stat().st_mode)!=0o600: raise ValueError("state_permissions")
    def _events(self):
        try: return [json.loads(x) for x in self.events_path.read_text(encoding="utf-8").splitlines() if x.strip()]
        except FileNotFoundError: return []
    def _write_state(self,count,event_count):
        data={"pilot_id":self.pilot_id,"target":self.target,"valid_evaluations":count,"terminal_state":"pending_analysis" if count>=self.target else "collecting","event_count":event_count,"events_sha256":hashlib.sha256(self.events_path.read_bytes()).hexdigest()}
        tmp=self.state_path.with_suffix(".tmp"); tmp.write_text(json.dumps(data,sort_keys=True,separators=(",",":")),encoding="utf-8"); os.chmod(tmp,0o600); os.replace(tmp,self.state_path)
    def reserve(self,turn_id): return bool(turn_id and not any(e.get("turn_id")==turn_id for e in self._events()) and self.snapshot().valid_evaluations<self.target)
    def append(self,event:Mapping[str,Any]):
        data=json.loads(json.dumps(dict(event),allow_nan=False))
        def scan(value):
            if isinstance(value,Mapping):
                for k,v in value.items():
                    if str(k).lower() in _FORBIDDEN_KEYS: raise ValueError("plaintext_field")
                    scan(v)
            elif isinstance(value,list):
                for v in value: scan(v)
            elif isinstance(value,str) and _SECRET_RE.search(value): raise ValueError("secret_like")
        scan(data); events=self._events()
        if data.get("pilot_id")!=self.pilot_id or data.get("turn_id") in {e.get("turn_id") for e in events}: raise ValueError("duplicate_event")
        expected=len(events)+1
        if data.get("ordinal",expected)!=expected: raise ValueError("ordinal_invalid")
        data.setdefault("ordinal",expected)
        with self.events_path.open("a",encoding="utf-8") as f: f.write(json.dumps(data,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n"); f.flush(); os.fsync(f.fileno())
        valid=sum(bool(e.get("counts_toward_target")) for e in events)+bool(data.get("counts_toward_target")); self._write_state(valid,expected)
    def snapshot(self):
        events=self._events(); valid=sum(bool(e.get("counts_toward_target")) for e in events)
        return PilotSnapshot(self.pilot_id,self.target,valid,len(events),"pending_analysis" if valid>=self.target else "collecting")
    def events(self): return self._events()

@dataclass(frozen=True)
class ShadowRuntimeConfig:
    pilot_id:str; policy:Any=None; max_queue:int=32; timeout:float=10.0
@dataclass(frozen=True)
class RetainOutcome:
    status:str; operation_ids_count:int=0; result_items_count:int|None=None; fact_count:int|None=None; fact_types:tuple[str,...]=(); latency_ms:int|None=None; error_code:str|None=None
class ShadowTransportError(RuntimeError):
    def __init__(self,code): self.code=code; super().__init__(code)
class TypeSafeTransport:
    def __init__(self,endpoint,api_key,timeout=10.0): self.url=endpoint.rstrip("/")+"/v1/systemone"; self.api_key=api_key; self.timeout=timeout
    def evaluate(self,payload):
        deadline=time.monotonic()+self.timeout; body=json.dumps(dict(payload),separators=(",",":")).encode(); last=None
        for attempt in range(2):
            remaining=deadline-time.monotonic()
            if remaining<=0: raise ShadowTransportError("timeout")
            req=urllib.request.Request(self.url,data=body,method="POST",headers={"Authorization":f"Bearer {self.api_key}","Content-Type":"application/json"})
            try:
                with urllib.request.urlopen(req,timeout=remaining) as response: raw=response.read()
                try: return json.loads(raw.decode())
                except (ValueError,UnicodeDecodeError): raise ShadowTransportError("invalid_json")
            except urllib.error.HTTPError as e:
                code="http_429" if e.code==429 else "http_5xx" if 500<=e.code<600 else "http_4xx" if 400<=e.code<500 else "transport_error"; last=ShadowTransportError(code)
                if code=="http_4xx": raise last
            except TimeoutError: last=ShadowTransportError("timeout")
            except OSError: last=ShadowTransportError("transport_error")
        raise last or ShadowTransportError("transport_error")
class JevShadowRuntime:
    def __init__(self,config,store,transport):
        import queue,threading
        from .jev_shadow import ShadowPolicy
        self.config=config; self.store=store; self.transport=transport; self.policy=config.policy or ShadowPolicy(); self._queue=queue.Queue(maxsize=config.max_queue); self._stop=threading.Event(); self._thread=None; self._lock=threading.Lock()
    def _ensure(self):
        import threading
        if self._thread is None: self._thread=threading.Thread(target=self._worker,daemon=True); self._thread.start()
    def _worker(self):
        while not self._stop.is_set() or not self._queue.empty():
            try: item=self._queue.get(timeout=.05)
            except Exception: continue
            try: self._evaluate(*item)
            finally: self._queue.task_done()
    def enqueue(self,turn_id,turns,*,explicit_memory_request):
        import queue
        if self._stop.is_set() or not self.store.reserve(turn_id): return False
        try: self._queue.put_nowait((turn_id,list(turns),explicit_memory_request))
        except queue.Full: return False
        self._ensure(); return True
    def _evaluate(self,turn_id,turns,explicit):
        from .jev_shadow import build_shadow_request,validate_shadow_response,derive_shadow_verdict,ShadowContractError,SHADOW_FAIL_OPEN
        event={"kind":"evaluation","pilot_id":self.store.pilot_id,"turn_id":turn_id,"valid":False,"counts_toward_target":False,"verdict":SHADOW_FAIL_OPEN,"model":None,"latency_ms":None,"usage":{},"error_code":None,"fact_count":0,"fact_types":[]}; start=time.monotonic()
        try:
            response=self.transport.evaluate(build_shadow_request(turns,self.policy)); answers=validate_shadow_response(response,self.policy); event.update(valid=True,counts_toward_target=True,verdict=derive_shadow_verdict(answers,explicit_memory_request=explicit,excluded_content=False),model=response.get("model"))
        except ShadowContractError as e: event["error_code"]=e.code
        except ShadowTransportError as e: event["error_code"]=e.code
        except TimeoutError: event["error_code"]="timeout"
        except Exception: event["error_code"]="transport_error"
        event["latency_ms"]=int((time.monotonic()-start)*1000)
        try: self.store.append(event)
        except ValueError: pass
    def record_retain_outcome(self,turn_id,outcome):
        if any(e.get("turn_id")==turn_id and e.get("kind")=="retain_outcome" for e in self.store.events()): return
        self.store.append({"kind":"retain_outcome","pilot_id":self.store.pilot_id,"turn_id":turn_id,"counts_toward_target":False,"status":outcome.status,"operation_ids_count":outcome.operation_ids_count,"result_items_count":outcome.result_items_count,"fact_count":outcome.fact_count,"fact_types":list(outcome.fact_types),"latency_ms":outcome.latency_ms,"error_code":outcome.error_code})
    def shutdown(self,timeout=1.0):
        if self._thread is None:return
        self._stop.set(); self._thread.join(max(0,timeout))
