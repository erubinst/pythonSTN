from datetime import datetime
import pandas as pd
import plotly.express as px
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

    df['display_start'] = df['start_lb']
    df['display_end'] = df['end_lb']

    return df


def display_current_schedule(tds, epoch_date_str):
    df = tds.export_to_df()
    df = convert_times_to_realtime(df, epoch_date_str)

    # if task name contains 'travel', set to different color
    df['type'] = 'task'
    df.loc[df['task_name'].str.contains('downtime'), 'type'] = 'downtime'
    # if has header or footer, set to downtime
    df.loc[df['task_name'].str.endswith('_header'), 'type'] = 'downtime'
    df.loc[df['task_name'].str.endswith('_footer'), 'type'] = 'downtime'
    df.loc[df['task_name'].str.startswith('travel'), 'type'] = 'travel'
    df.loc[df['task_name'].str.startswith('pickup_from_'), 'type'] = 'ztransport'
    df.loc[df['task_name'].str.startswith('dropoff_at_'), 'type'] = 'ztransport'

    # sort the df so that all the type transport are last
    df = df.sort_values('type')

    df["type"] = df["type"].astype(str)
    color_discrete_map = {
        "task": "#00008B",
        "travel": "#FFFF00",
        "transport": "#FFB269",
        "downtime": "#A9A9A9",
    }

    df['resource'] = df['resource'].astype(str)
    df = df.sort_values('resource')

    # ---------------------------------------------------------
    # ADD SMALL ARTIFICIAL DURATION FOR ZERO-LENGTH TASKS
    # ---------------------------------------------------------
    epsilon = pd.Timedelta(minutes=0.5)  # can be 1s or 30s if you prefer smaller

    df['end_lb_plot'] = df['display_end']  # new plotting end time
    zero_mask = df['display_start'] == df['display_end']
    df.loc[zero_mask, 'end_lb_plot'] = df.loc[zero_mask, 'end_lb'] + epsilon
    # ---------------------------------------------------------

    fig = px.timeline(
        df,
        x_start="display_start",
        x_end="end_lb_plot",  # <<< use modified end time here
        y="resource",
        text="task_name",
        color="type",
        hover_data={
            "display_start": False,
            "display_end": False,
            "resource": False,
            "start_lb": True,
            "start_ub": True,
            "end_lb": True,
            "end_ub": True,
            "capability": True,
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


def export_schedule_to_df(tds, epoch_date_str):
    df = tds.export_to_df()
    df = convert_times_to_realtime(df, epoch_date_str)
    # remove following columns: capability, start_lb_time, end_lb_time, start_ub_time, end_ub_time
    # df = df.drop(columns=['capability', 'start_lb_time', 'end_lb_time', 'start_ub_time', 'end_ub_time'])
    #df = df[~df['task_name'].str.contains('downtime')]
    #df = df[~df['task_name'].str.endswith('_header')]
    #df = df[~df['task_name'].str.endswith('_footer')]
    return df

def export_schedule_to_csv(tds, epoch_date_str):
    df = export_schedule_to_df(tds, epoch_date_str)
    df.to_csv("tds_schedule_export.csv", index=False)
