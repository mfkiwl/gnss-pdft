#!/usr/bin/env python3

"""
Data collection program for qerr capture.
"""

import datetime
import argparse
from serial import Serial
from pyubx2 import UBXReader, UBX_PROTOCOL, UBXMessage, SET_LAYER_RAM, POLL_LAYER_RAM, TXN_COMMIT, TXN_NONE
from qerr_utils import *

def poll_config(device):
    # Poll configuration of "CFG_MSGOUT_UBX_TIM_TP_USB". On startup, should be 0 by default.
    layer = POLL_LAYER_RAM
    position = 0
    keys = ["CFG_MSGOUT_UBX_TIM_TP_USB", "CFG_MSGOUT_UBX_NAV_TIMEUTC_USB"]
    msg = UBXMessage.config_poll(layer, position, keys)
    print(msg)
    with Serial(device, 38400, timeout=3) as stream:
        stream.write(msg.serialize())
        ubr_poll_status = UBXReader(stream, protfilter=UBX_PROTOCOL)
        raw_data, parsed_data = ubr_poll_status.read()
        if parsed_data is not None:
            print(parsed_data)


def set_config(device):
    layer = SET_LAYER_RAM
    transaction = TXN_NONE

    cfgData = [("CFG_MSGOUT_UBX_TIM_TP_USB", 1), ("CFG_MSGOUT_UBX_NAV_TIMEUTC_USB", 1)]
    msg = UBXMessage.config_set(layer, transaction, cfgData)
    print(msg)
    with Serial(device, BAUDRATE, timeout=100) as stream:
        stream.write(msg.serialize())
        ubr = UBXReader(stream, protfilter=UBX_PROTOCOL)
        for i in range(3):
            raw_data, parsed_data = ubr.read()
            if parsed_data is not None:
                print(parsed_data)

def verify_dataflow(device, timeout=10):
    """Verify packets of desired types are being received."""
    packet_id_flags = {
        'NAV-TIMEUTC': False,
        'TIM-TP': False
    }
    with Serial(device, BAUDRATE, timeout=timeout) as stream:
        ubr = UBXReader(stream, protfilter=UBX_PROTOCOL)
        for i in range(1000):
            raw_data, parsed_data = ubr.read()
            if parsed_data:
                for packet_id in packet_id_flags.keys():
                    if parsed_data.identity == packet_id:
                        packet_id_flags[packet_id] = True
            if all(packet_id_flags.values()):
                return True
    raise Exception(f'Not all packets are being received. Check the following for details: {packet_id_flags}')



def collect_data(df_refs, device, timeout=10):
    with Serial(device, BAUDRATE, timeout=timeout) as stream:
        ubr = UBXReader(stream, protfilter=UBX_PROTOCOL)
        # Cache for saving packets and timestamping their arrival.
        packet_cache = {
            'NAV-TIMEUTC': {
                'valid': False,
                'timestamp': None,
                'parsed_data': None,
            },
            'TIM-TP': {
                'valid': False,
                'timestamp': None,
                'parsed_data': None,
            }
        }
        while True:
            # Wait for next packet
            raw_data, parsed_data = ubr.read()
            pkt_unix_timestamp = datetime.datetime.now()
            # Add parsed data to cache
            if parsed_data:
                if parsed_data.identity == 'NAV-TIMEUTC':
                    # UBX-NAV-TIMEUTC
                    packet_cache['NAV-TIMEUTC']['valid'] = True
                    packet_cache['NAV-TIMEUTC']['timestamp'] = pkt_unix_timestamp
                    packet_cache['NAV-TIMEUTC']['parsed_data'] = {
                        'pkt_unix_timestamp_NAV-TIMEUTC': pkt_unix_timestamp,
                        'iTOW (ms)':        parsed_data.iTOW,
                        'tAcc (ns)':        parsed_data.tAcc,
                        'year':             parsed_data.year,
                        'month':            parsed_data.month,
                        'day':              parsed_data.day,
                        'hour':             parsed_data.hour,
                        'min':              parsed_data.min,
                        'sec':              parsed_data.sec,
                        'validTOW_flag':    parsed_data.validTOW,
                        'validWKN_flag':    parsed_data.validWKN,
                        'validUTC_flag':    parsed_data.validUTC,
                        'utcStandard_NAV-TIMEUTC':  parsed_data.utcStandard
                    }
                elif parsed_data.identity == 'TIM-TP':
                    # UBX-TIM-TP
                    packet_cache['TIM-TP']['valid'] = True
                    packet_cache['TIM-TP']['timestamp'] = pkt_unix_timestamp
                    packet_cache['TIM-TP']['parsed_data'] = {
                        'pkt_unix_timestamp_TIM-TP': pkt_unix_timestamp,
                        'towMS (ms)':           parsed_data.towMS,
                        'towSubMS':             parsed_data.towSubMS,
                        'qErr (ps)':            parsed_data.qErr,
                        'week (weeks)':         parsed_data.week,
                        'timeBase_flag':        parsed_data.timeBase,
                        'utc_flag':             parsed_data.utc,
                        'raim_flag':            parsed_data.raim,
                        'qErrInvalid_flag':     parsed_data.qErrInvalid,
                        'timeRefGnss':          parsed_data.timeRefGnss,
                        'utcStandard_TIM-TP':   parsed_data.utcStandard
                    }

            # Check if packet cache is full.
            if packet_cache['TIM-TP']['valid'] and packet_cache['NAV-TIMEUTC']['valid']:
                # Verify that packet timestamps differ by no more than 1s.
                time_diff = abs(packet_cache['TIM-TP']['timestamp'] - packet_cache['NAV-TIMEUTC']['timestamp'])
                microsec_time_diff = time_diff.seconds * 1e6 + time_diff.microseconds
                if microsec_time_diff < 1e6:
                    # Merge packet data into a single dict
                    merged_data = {
                        **packet_cache['TIM-TP']['parsed_data'],
                        **packet_cache['NAV-TIMEUTC']['parsed_data']
                    }
                    # merged_data['pkt_unix_timestamp'] = pkt_unix_timestamp # Overwrite individual timestamps with merged timestamp.
                    # Verify merged data schema matches pandas schema.
                    merged_keys = set(merged_data.keys())
                    schema_keys = set(df_refs['MERGED'].columns.tolist())
                    if merged_keys != schema_keys:
                        raise KeyError(
                            'packet keys do not match data schema:'
                            '\nPacket keys: {}\nSchema: {}.\n '.format(
                                merged_keys - schema_keys, schema_keys - merged_keys
                            )
                        )
                    # Do write transaction
                    df_refs['MERGED'].loc[len(df_refs['MERGED'])] = merged_data
                    df_refs['TIM-TP'].loc[len(df_refs['TIM-TP'])] = packet_cache['TIM-TP']['parsed_data']
                    df_refs['NAV-TIMEUTC'].loc[len(df_refs['NAV-TIMEUTC'])] = packet_cache['NAV-TIMEUTC'][
                        'parsed_data']
                    # Reset cache
                    packet_cache['TIM-TP']['valid'] = False
                    packet_cache['NAV-TIMEUTC']['valid'] = False
                    print('Collection stats:'
                          '\tMERGED: {:6d} '
                          '\tTIM-TP: {:6d} '
                          '\tNAV-TIMEUTC: {:6d}'
                          ''.format(len(df_refs['MERGED']), len(df_refs['TIM-TP']), len(df_refs['NAV-TIMEUTC'])), end='\r')
                else:
                    # Drop the earlier packet from merge if time diff is too great.
                    # However, save packet to individual df anyway to prevent data loss.
                    if packet_cache['TIM-TP']['timestamp'] < packet_cache['NAV-TIMEUTC']['timestamp']:
                        df_refs['TIM-TP'].loc[len(df_refs['TIM-TP'])] = packet_cache['TIM-TP']['parsed_data']
                        packet_cache['TIM-TP']['valid'] = False
                    else:
                        df_refs['NAV-TIMEUTC'].loc[len(df_refs['NAV-TIMEUTC'])] = packet_cache['NAV-TIMEUTC']['parsed_data']
                        packet_cache['NAV-TIMEUTC']['valid'] = False

def start(device):
    # Configure device and ensure all desired packets are being received.
    poll_config(device)
    set_config(device)
    poll_config(device)
    verify_dataflow(device)     # Will throw Exception if not all packet types are being received.
    # Create dataframes
    df_refs = {
        'NAV-TIMEUTC':  create_empty_df('NAV-TIMEUTC'),
        'TIM-TP':       create_empty_df('TIM-TP'),
        'MERGED':       create_empty_df('MERGED')
    }

    # Start data collection
    start_timestamp = datetime.datetime.now().isoformat()
    experiment_dir = get_experiment_dir(start_timestamp)
    os.makedirs(experiment_dir, exist_ok=False)
    print('Starting data collection. To stop collection, use CTRL+C.'
          '\nStart timestamp: {}'.format(start_timestamp))
    try:
        collect_data(df_refs, device)
    except KeyboardInterrupt:
        print('Stopping data collection.')
        pass
    finally:
        # Save data
        for data_type in df_refs.keys():
            fpath = f'{experiment_dir}/data-type_{data_type}.start_{start_timestamp}'
            save_data(df_refs[data_type], fpath)
        print('Data saved in {}'.format(experiment_dir))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('device',
                        help='specify the device path. example: /dev/ttyS3',
                        type=str,
                        )
    args = parser.parse_args()
    if args.device is not None:
        if os.path.exists(args.device):
            start(args.device)
        else:
            print(f'Cannot access {args.device}')

