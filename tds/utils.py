from datetime import datetime
import pandas as pd
import plotly.express as px

def minutes_since_cz(timestamp_str, cz_datetime):
    """Convert ISO datetime string to minutes since cz."""
    dt = datetime.fromisoformat(timestamp_str)
    delta = dt - cz_datetime
    return int(delta.total_seconds() / 60)


def execute_undo_functions(undo_info):
    while undo_info: # pop in LIFO order
        undo_fn_info = undo_info.pop()
        if isinstance(undo_fn_info, tuple):
            name, undo_fn = undo_fn_info
            undo_fn()
        else:
            undo_fn_info()


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

    # if task name contains 'travel', set to different color
    df['type'] = 'task'
    df.loc[df['task_name'].str.startswith('travel'), 'type'] = 'travel'
    df.loc[df['task_name'].str.startswith('pickup_from_'), 'type'] = 'transport'
    df.loc[df['task_name'].str.startswith('dropoff_at_'), 'type'] = 'transport'
    df["type"] = df["type"].astype(str)

    color_discrete_map = {
        "task": "#00008B",
        "travel": "#FFFF00",
        "transport": "#FFB269",
    }

    df['resource'] = df['resource'].astype(str)
    df = df.sort_values('resource')

    # ---------------------------------------------------------
    # ADD SMALL ARTIFICIAL DURATION FOR ZERO-LENGTH TASKS
    # ---------------------------------------------------------
    epsilon = pd.Timedelta(minutes=0.5)  # can be 1s or 30s if you prefer smaller

    df['end_lb_plot'] = df['end_lb']  # new plotting end time
    zero_mask = df['start_lb'] == df['end_lb']
    df.loc[zero_mask, 'end_lb_plot'] = df.loc[zero_mask, 'end_lb'] + epsilon
    # ---------------------------------------------------------

    fig = px.timeline(
        df,
        x_start="start_lb",
        x_end="end_lb_plot",  # <<< use modified end time here
        y="resource",
        text="task_name",
        color="type",
        hover_data={
            "start_lb": False,
            "end_lb": False,
            "resource": False,
            "start_lb_time": True,
            "start_ub_time": True,
            "end_lb_time": True,
            "end_ub_time": True,
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
