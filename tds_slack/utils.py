from datetime import datetime
import pandas as pd
import plotly.express as px
import altair as alt
import json


def minutes_since_cz(timestamp_str, cz_datetime):
    """Convert ISO datetime string to minutes since cz."""
    dt = datetime.fromisoformat(timestamp_str)
    delta = dt - cz_datetime
    return int(delta.total_seconds() / 60)


def path_to_dict(path):
    with open(path, "r") as f:
        data = json.load(f)
    return data


def execute_undo_functions(undo_info):
    while undo_info: # pop in LIFO order
        undo_fn_info = undo_info.pop()
        if isinstance(undo_fn_info, tuple):
            name, undo_fn = undo_fn_info
            undo_fn()
        else:
            undo_fn_info()


def convert_times_to_realtime(df, epoch_date_str):
    # set timezone to utc
    epoch_date = pd.to_datetime(epoch_date_str)
    df['start_lb'] = epoch_date + pd.to_timedelta(df['start_lb'], unit='m')
    df['end_lb'] = epoch_date + pd.to_timedelta(df['end_lb'], unit='m')
    df['start_ub'] = epoch_date + pd.to_timedelta(df['start_ub'], unit='m')
    df['end_ub'] = epoch_date + pd.to_timedelta(df['end_ub'], unit='m')


    df['start_lb'] = df['start_lb'].dt.tz_localize('UTC')
    df['end_lb'] = df['end_lb'].dt.tz_localize('UTC')
    df['start_ub'] = df['start_ub'].dt.tz_localize('UTC')
    df['end_ub'] = df['end_ub'].dt.tz_localize('UTC')

    df["start_lb_time"] = df["start_lb"].dt.strftime("%H:%M")
    df["end_lb_time"]   = df["end_lb"].dt.strftime("%H:%M")
    df["start_ub_time"] = df["start_ub"].dt.strftime("%H:%M")
    df["end_ub_time"]   = df["end_ub"].dt.strftime("%H:%M")

    return df


#TODO update for slack scenario
def display_current_schedule(tds, epoch_date_str=None):
    df = tds.export_to_df()

    # if task name contains 'travel', set to different color
    df['type'] = 'task'
    df.loc[df['task_name'].str.contains('downtime'), 'type'] = 'downtime'
    # if has header or footer, set to downtime
    df.loc[df['task_name'].str.endswith('_header'), 'type'] = 'downtime'
    df.loc[df['task_name'].str.endswith('_footer'), 'type'] = 'downtime'
    df.loc[df['task_name'].str.startswith('travel'), 'type'] = 'travel'

    # filter out header/footer tasks for visualization (EXCEPT those containing travel in the name)
    df = df[~((df['task_name'].str.endswith('_header') | df['task_name'].str.endswith('_footer')) & ~df['task_name'].str.contains('travel'))]

    # Convert integer minutes to datetime from epoch date
    # set epoch to TODAY
    epoch = pd.to_datetime(epoch_date_str) if epoch_date_str else pd.to_datetime("today").normalize()
    df['start_lb_dt'] = epoch + pd.to_timedelta(df['start_lb'], unit='m')
    df['end_lb_dt'] = epoch + pd.to_timedelta(df['end_lb'], unit='m')
    df['start_ub_dt'] = epoch + pd.to_timedelta(df['start_ub'], unit='m')
    df['end_ub_dt'] = epoch + pd.to_timedelta(df['end_ub'], unit='m')

    # sort the df so that all the type transport are last
    df = df.sort_values('type')

    df["type"] = df["type"].astype(str)
    color_discrete_map = {
        "task": "#00008B",
        "travel": "#FFFF00",
        "downtime": "#A9A9A9",
    }

    df['resource'] = df['resource'].astype(str)
    # sort resources alphabetically for better visualization
    df = df.sort_values('resource', ascending=True)

    fig = px.timeline(
        df,
        x_start="start_lb_dt",
        x_end="end_lb_dt",
        y="resource",
        text="task_name",
        color="type",
        hover_data={
            "start_lb": True,
            "start_ub": True,
            "end_lb": True,
            "end_ub": True,
            "capability": True,
            "location": True,
            "slot_flexibility": True,
            "slack": True
        },
        color_discrete_map=color_discrete_map
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
        textfont_size=20
    )

    fig.show()


def export_schedule_to_df(tds, epoch_date_str=None):
    df = tds.export_to_df()
    if epoch_date_str:
        df = convert_times_to_realtime(df, epoch_date_str)
    return df


def export_schedule_to_csv(tds, epoch_date_str):
    df = export_schedule_to_df(tds, epoch_date_str)
    df.to_csv("tds_schedule_export.csv", index=False)
