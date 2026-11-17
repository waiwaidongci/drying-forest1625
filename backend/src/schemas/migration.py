from pydantic import BaseModel,Field
class SweepInput(BaseModel):
    batch_size:int=Field(default=100,ge=1,le=500)
class ResolveInput(BaseModel):
    ids:list[int]=Field(min_length=1,max_length=1000)
    unit:str=Field(min_length=3,max_length=20)
