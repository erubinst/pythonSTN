import json
from datetime import datetime
import pandas as pd
from resource import Resource
from task import Task
from tds_manager import TDSManager
from config import *
import plotly.express as px


def minutes_since_cz(timestamp_str, cz_datetime):
    """Convert ISO datetime string to minutes since cz."""
    dt = datetime.fromisoformat(timestamp_str)
    delta = dt - cz_datetime
    return int(delta.total_seconds() / 60)

def load_request_data(request_path):
    """Load request.json and return dicts for templates, orders, and resources."""
    with open(request_path, "r") as f:
        request_data = json.load(f)

    templates = {t["name"]: t for t in request_data.get("templates", [])}
    orders = {o["name"]: o for o in request_data.get("orders", [])}
    resources = {r["name"]: r for r in request_data.get("resourceTypes", [])}

    return {
        "templates": templates,
        "orders": orders,
        "resources": resources,
        "order_constraints": request_data.get("order-constraints", [])
    }

def load_resources_df(resources_dict):
    """Return a DataFrame of resources and their capabilities."""
    rows = []
    for name, r in resources_dict.items():
        rows.append({
            "resource_name": name,
            "capabilities": ", ".join(r.get("capabilities", []))
        })
    return pd.DataFrame(rows)

def load_tasks_df(templates_dict, orders_dict, cz_datetime):
    """Return a DataFrame of tasks/orders with required capabilities, est, lft, duration."""
    rows = []
    for order_name, order in orders_dict.items():
        template_name = order["tasks"][0]  # assume one template per order
        template = templates_dict.get(template_name)
        if not template:
            continue
        subtask = template["subtasks"][0]  # assume one subtask per template
        rows.append({
            "task_name": order_name,
            "required_capabilities": ", ".join(subtask.get("requiredCapabilities", [])),
            "est": minutes_since_cz(order["earlieststartdate"], cz_datetime),
            "lft": minutes_since_cz(order["duedate"], cz_datetime),
            "duration": subtask.get("duration", None)
        })
    return pd.DataFrame(rows)

def load_resources_and_tasks(request_path, cz_datetime_str="2025-05-19T00:00"):
    cz_datetime = datetime.fromisoformat(cz_datetime_str)
    request_data = load_request_data(request_path)

    resources_df = load_resources_df(request_data["resources"])
    tasks_df = load_tasks_df(request_data["templates"], request_data["orders"], cz_datetime)

    return resources_df, tasks_df


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
        task = Task(
            name=name,
            capabilities=capabilities,
            tds_manager=tds_manager,
            assigned_resources=None  # no assignments yet
        )

        task.add_time_window_constraints(row.get('est'), row.get('lft'))
        task.add_duration_constraint(row.get('duration'))


def load_initial_timelines_from_manager(initial_schedule_path, tds_manager):
    """
    Append tasks to each resource's timeline based on initial_schedule.json.
    Looks up tasks directly from tds_manager.tasks.

    Parameters:
        initial_schedule_path (str): path to the initial schedule JSON
        resource_map (dict): resource_name -> Resource instance
        tds_manager (TDSManager): TDS manager with tasks already loaded
    """
    with open(initial_schedule_path, "r") as f:
        schedule_data = json.load(f)

    for res_entry in schedule_data.get("resources", []):
        res_name = res_entry["resourceName"]
        if res_name not in tds.resources:
            print(f"Warning: resource '{res_name}' not found; skipping timeline")
            continue

        resource = tds.resources[res_name]

        for tentry in res_entry.get("timeline", []):
            order_name = tentry["order"]
            task = tds_manager.tasks.get(order_name)
            if not task:
                print(f"Warning: task/order '{order_name}' not found in TDS manager; skipping")
                continue
            # Append task to the resource's timeline
            resource.append_task_to_timeline(task, tentry['capability'])

def display_current_schedule(tds, epoch_date_str):
    df = tds.export_to_df()
    epoch_date = pd.to_datetime(epoch_date_str)
    df['start_lb'] = epoch_date + pd.to_timedelta(df['start_lb'], unit='m')
    df['end_lb'] = epoch_date + pd.to_timedelta(df['end_lb'], unit='m')
    df['start_ub'] = epoch_date + pd.to_timedelta(df['start_ub'], unit='m')
    df['end_ub'] = epoch_date + pd.to_timedelta(df['end_ub'], unit='m')

    df["start_lb_time"] = df["start_lb"].dt.strftime("%H:%M")
    df["end_lb_time"]   = df["end_lb"].dt.strftime("%H:%M")
    df["start_ub_time"] = df["start_ub"].dt.strftime("%H:%M")
    df["end_ub_time"]   = df["end_ub"].dt.strftime("%H:%M")

    df['resource'] = df['resource'].astype(str)
    df = df.sort_values('resource')
    fig = px.timeline(
        df,
        x_start="start_lb",
        x_end="end_lb",
        y="resource",
        text = "task_name",
        hover_data={
            "start_lb": False,
            "end_lb": False,
            "resource": False,
            "start_lb_time": True,
            "start_ub_time": True,
            "end_lb_time": True,
            "end_ub_time": True,
            "capability": True,
        }
    )
    fig.update_layout(
        title="Current Schedule",
        xaxis_title="Time",
        yaxis_title="Resource",
        height=600,
    )
    for trace in fig.data:
        if 'text' in trace:
            trace.textposition = 'inside'

    fig.update_traces(
        textposition='inside',
        insidetextanchor='middle',
        textfont_size=20  # Increase number for bigger text
    )
    fig.show()


resources_df, tasks_df = load_resources_and_tasks(REQUEST_PATH)
tds = TDSManager()
add_resources_to_tds(resources_df, tds)
add_tasks_to_tds(tasks_df, tds)
load_initial_timelines_from_manager(INITIAL_SCHEDULE_PATH, tds)
display_current_schedule(tds, EPOCH_DATE)

