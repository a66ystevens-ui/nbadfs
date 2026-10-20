"""Optional append-only incremental checkpoints in a private Supabase Storage bucket."""
import re
from datetime import datetime, timezone
from uuid import uuid4

import httpx

from .backup import export_checkpoint, restore_checkpoint


class CloudArchiveError(Exception):
    pass


class SupabaseArchive:
    def __init__(self,url,key,bucket='courtvision',prefix='owner',client=None):
        if not re.fullmatch(r'https://[a-z0-9-]+\.supabase\.co',url.rstrip('/')):
            raise ValueError('Use the HTTPS project URL from Supabase settings')
        if not all(re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',v) for v in [bucket,prefix]):
            raise ValueError('Bucket and archive namespace must contain letters, digits, underscores or hyphens')
        self.base=url.rstrip('/')+'/storage/v1'
        self.key=key
        self.bucket=bucket
        self.prefix=prefix
        self.client=client

    def _call(self,method,path,**kwargs):
        client=self.client or httpx.Client(timeout=30)
        try:
            response=client.request(method,self.base+path,
                headers={'apikey':self.key,'Authorization':'Bearer '+self.key,**kwargs.pop('headers',{})},**kwargs)
            if not response.is_success:
                raise CloudArchiveError(f'Cloud archive returned HTTP {response.status_code}; check the private bucket and server credentials')
            return response
        except httpx.HTTPError:
            raise CloudArchiveError('Cloud archive connection failed') from None
        finally:
            if self.client is None:
                client.close()

    def checkpoint(self,known):
        payload,keys,count=export_checkpoint(known)
        if count:
            name=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+uuid4().hex+'.json.gz'
            self._call('POST',f'/object/{self.bucket}/{self.prefix}/{name}',content=payload,
                       headers={'Content-Type':'application/gzip','x-upsert':'false'})
        return keys,count

    def restore(self):
        names=[]
        offset=0
        while True:
            response=self._call('POST',f'/object/list/{self.bucket}',json={
                'prefix':self.prefix,'limit':1000,'offset':offset,'sortBy':{'column':'name','order':'asc'}})
            try:
                rows=response.json()
                if not isinstance(rows,list):
                    raise ValueError()
                for row in rows:
                    name=row['name']
                    if not re.fullmatch(r'\d{8}T\d{12}Z-[a-f0-9]{32}\.json\.gz',name):
                        raise ValueError()
                    names.append(name)
            except (ValueError,TypeError,KeyError):
                raise CloudArchiveError('Unexpected objects in archive namespace; use a dedicated empty namespace') from None
            if len(rows)<1000:
                break
            offset+=1000
        count=0
        for name in sorted(names):
            response=self._call('GET',f'/object/{self.bucket}/{self.prefix}/{name}')
            try:
                count+=restore_checkpoint(response.content)
            except (ValueError,TypeError):
                raise CloudArchiveError('A cloud checkpoint could not be restored safely') from None
        _,known,_=export_checkpoint()
        return known,count
