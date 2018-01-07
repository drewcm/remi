import os
import json

cfg = {}

if not cfg:
    curr_dir = os.path.dirname(__file__)
    #cfg_path = os.path.join(curr_dir, '../instance/config.json')
    cfg_path = os.environ['REMI_CONFIG']
    with open(cfg_path) as json_config_file:
        cfg = json.load(json_config_file)
