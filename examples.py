from stn import STN
import json
import numpy as np

file_path = "examples/test.json"
#load json
with open(file_path, "r") as f:
    data = json.load(f)


def add_task(stn, task_name, duration=0, start=0, end=np.inf):
    start_tp = f"{task_name}_start"
    end_tp = f"{task_name}_end"
    stn.add_timepoint(start_tp)
    stn.add_timepoint(end_tp)
    stn.add_constraint(start_tp, end_tp, duration, duration)
    stn.add_constraint(stn.cz, start_tp, start)
    stn.add_constraint(stn.cz, end_tp, 0, end)



stn = STN()
for task in data["tasks"]:
    add_task(stn, task['task_name'], task['duration'], task['release_time'], task['due_time'])
print(stn)
stn.add_constraint("Lawnmowing_end", "Cooking_start", 12) 
print(stn)


