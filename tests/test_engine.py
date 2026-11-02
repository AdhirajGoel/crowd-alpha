"""Both adapters enforce structured output and keep source credentials out of child environments."""
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import engine
schema={'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False}
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp)
    for provider in ('codex','claude'):
        commands=[]
        class Process:
            returncode=0
            def __init__(self,cmd,**kwargs):
                self.cmd=cmd;commands.append(cmd)
                assert 'X_BEARER_TOKEN' not in kwargs['env']
            def communicate(self,payload,timeout):
                assert timeout==3600
                if provider=='codex':
                    assert 'untrusted evidence' in payload
                    Path(self.cmd[self.cmd.index('-o')+1]).write_text('{"ok":true}')
                    return '',None
                message=json.loads(payload)
                assert message['message']['content'][0]['type']=='text'
                return json.dumps({'type':'result','subtype':'success','structured_output':{'ok':True}}),None
        config={'engine':provider,'executable':provider,'model':'test-model'}
        with patch.object(engine,'resolve',return_value=(config,'test-version')),patch.object(engine.subprocess,'Popen',Process),patch.dict(engine.os.environ,{'X_BEARER_TOKEN':'fixture-secret'}):
            assert engine.generate(root/provider,'brief','test',{},schema,config)=={'ok':True}
            assert engine.generate(root/provider,'brief','test',{},schema,config)=={'ok':True}
        assert len(commands)==1
        if provider=='claude':
            assert commands[0][commands[0].index('--output-format')+1]=='stream-json'
            assert '--verbose' in commands[0]
            assert '--safe-mode' in commands[0] and commands[0][commands[0].index('--tools')+1]==''
        else:assert commands[0][commands[0].index('--sandbox')+1]=='read-only'
        (root/provider/'bad.json').write_text('{"ok":"wrong"}')
        try:engine.generate(root/provider,'bad','test',{},schema,config)
        except engine.jsonschema.ValidationError:pass
        else:raise AssertionError('Invalid cached output accepted')
print('PASS: both CLI adapters, isolation, structured validation and cache reuse')
