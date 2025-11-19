import json
from resource import Resource
from task import Task
from tds_manager import TDSManager
from config import *
from parse import *


def add_resources_to_tds(resources_df, tds_manager):
    """
    Create Resource objects from resources_df and add them to the TDS manager.

    Parameters:
        resources_df (pd.DataFrame): DataFrame with columns ['resource_name', 'capabilities']
        tds_manager: initialized TDS manager object
    """
    for _, row in resources_df.iterrows():
        name = row["resource_name"]
        caps = [c.strip() for c in row["capabilities"].split(",")] if row["capabilities"] else []
        res = Resource(name, caps, tds_manager)


def add_tasks_to_tds(tasks_df, tds_manager):
    """
    Create Task objects from tasks_df and add them to the TDS manager.
    Does NOT assign resources yet.

    Parameters:
        tasks_df (pd.DataFrame): columns = ['task_name', 'required_capabilities', 'est', 'lft', 'duration']
        tds_manager: initialized TDS manager object
    """
    for _, row in tasks_df.iterrows():
        name = row["task_name"]
        capabilities = [c.strip() for c in row["required_capabilities"].split(",")] if row["required_capabilities"] else []
        try:
            task = Task(
                name=name,
                capabilities=capabilities,
                tds_manager=tds_manager,
                order=None, #TODO set order
                template=None, #TODO set template
                assigned_resources=None  # no assignments yet
            )
        except ValueError as e:
            print(f"Error creating task '{name}': {e}")
            continue

        task.add_time_window_constraints(row.get('est'), row.get('lft'))
        task.add_duration_constraint(row.get('duration'))

# TODO: implement order constraints loading


def load_initial_timelines_to_tds(df, tds_manager):
    """
    Append tasks to each resource's timeline using a schedule DataFrame.

    Parameters:
        df (DataFrame): DataFrame produced by schedule_json_to_df()
        tds_manager (TDSManager): TDS manager with tasks & resources loaded
    """

    for _, row in df.iterrows():
        res_name = row["resourceName"]
        order_name = row["order"]
        capability = row["capability"]

        if res_name not in tds_manager.resources:
            print(f"Warning: resource '{res_name}' not found; skipping timeline")
            continue

        resource = tds_manager.resources[res_name]

        task = tds_manager.tasks.get(order_name)
        if not task:
            print(f"Warning: task/order '{order_name}' not found in TDS manager; skipping")
            continue

        try:
            resource.append_task_to_timeline(task, capability)
        except ValueError as e:
            print(f"Error appending task '{order_name}' to resource '{res_name}': {e}")




resources_df, tasks_df = load_resources_and_tasks(REQUEST_PATH)
tds = TDSManager()
add_resources_to_tds(resources_df, tds)
add_tasks_to_tds(tasks_df, tds)
init_schedule = schedule_json_to_df(INITIAL_SCHEDULE_PATH)
load_initial_timelines_to_tds(init_schedule, tds)
#display_current_schedule(tds, EPOCH_DATE)

