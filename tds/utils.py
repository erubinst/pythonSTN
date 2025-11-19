from datetime import datetime
import pandas as pd

def minutes_since_cz(timestamp_str, cz_datetime):
    """Convert ISO datetime string to minutes since cz."""
    dt = datetime.fromisoformat(timestamp_str)
    delta = dt - cz_datetime
    return int(delta.total_seconds() / 60)


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