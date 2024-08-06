import pandas as pd

from pathlib import Path
from tp_raw_event_reader import *


def read_all_scoreboards(scoreboards_folder = 'scoreboards/match'):
    """
    """
    scoreboard_paths = Path.cwd().joinpath(scoreboards_folder).glob('*.csv')

    all_scoreboards = []
    for scoreboard_file in scoreboard_paths:
        scoreboard_df = pd.read_csv(scoreboard_file, index_col = None, header = 0)
        all_scoreboards.append(scoreboard_df)

    return pd.concat(all_scoreboards, axis = 0, ignore_index = True)


def read_player_scoreboards(player,
                            scoreboards_folder = 'scoreboards/match',
                            name_key = 'name'):
    """
    """
    scoreboard_paths = Path.cwd().joinpath(scoreboards_folder).glob('*.csv')

    all_scoreboards = []
    for scoreboard_file in scoreboard_paths:
        scoreboard_df = pd.read_csv(scoreboard_file)
        scoreboard_player = scoreboard_df.loc[scoreboard_df[name_key] == player]
        if not scoreboard_player.empty:
            all_scoreboards.append(scoreboard_player)

    return pd.concat(all_scoreboards, axis = 0, ignore_index = True)
            

def save_player_scoreboards(scoreboards, playername,
                            scoreboards_folder = 'scoreboards/player'):
    """
    """
    scoreboard_path = Path.cwd().joinpath(scoreboards_folder).joinpath(f'{playername}.csv')
    scoreboard_path.parent.mkdir(parents = True, exist_ok = True)
    scoreboards.to_csv(scoreboard_path, index = False)


if __name__ == '__main__':
    playername = 'Threeflower'
    player_scoreboards = filter_player_scoreboards(player = playername)
    save_player_scoreboards(scoreboards = player_scoreboards, playername = playername)
