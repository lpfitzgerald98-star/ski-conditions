"""Central, tunable configuration for the ski-conditions tracker.

Everything that you might want to re-tune once you see real output lives here:
mountain -> station/grid mappings, the percentile -> letter-grade curve, storm
thresholds, and the data-quality knobs. Nothing about grading is hardcoded in
the compute path -- change a number here and re-run `report`, no DB migration.
"""

# ---------------------------------------------------------------------------
# Mountains
# ---------------------------------------------------------------------------
# Each resort maps to a historical snow station (the grading baseline) and, in the
# US, its NWS grid point (forecast + "what's coming"). `data_source` selects the
# historical network (see pipeline.SOURCES); each has its own station-id key:
#   * "snotel" (default) -> NRCS SNOTEL, the US West. `snotel_station` triplet
#     "<id>:<state>:SNTL"; SWE -> swe_gain metric.
#   * "cdec"   -> California CDEC, the eastern Sierra. `cdec_station`; SWE pillows.
#   * "bcsws"  -> BC Automated Snow Weather Stations. `bcsws_station`; SWE pillows.
#   * "acis"   -> NOAA ACIS/COOP, the US Northeast. `acis_sid` (GHCN); no SWE ->
#     new_snow (daily snowfall) metric.
#   * "eccc"   -> Environment Canada COOP. `eccc_station` (climate id); new_snow.
# NWS is US-only: Canadian (eccc/bcsws) mountains carry no grid and score on
# history + base alone. NWS grid is (office, gridX, gridY) from
# https://api.weather.gov/points/<lat>,<lon>.
#
# `verified` marks whether the station<->resort pairing and grid were confirmed
# against the live APIs. Every entry's NWS grid was resolved live and its station
# picked from live station metadata; `verified: False` flags the few where the
# station is a real compromise (distance/elevation) worth revisiting.
MOUNTAINS = {
    "alta": {
        "name": "Alta, UT",
        "snotel_station": "766:UT:SNTL",
        "snotel_name": "Snowbird",
        "nws_office": "SLC",
        "nws_grid": (108, 167),
        "latitude": 40.5883,
        "longitude": -111.6386,
        "verified": True,
        # Little Cottonwood is a high-snowfall climate; a "good day" bar. An East
        # Coast hill added later would set this far lower (e.g. {24: 4, 72: 8}).
        "storm_floor_inches": {24: 8, 72: 15},
        # Core lift-served season as (month, day). Drives dynamic score weighting.
        # A Southern-Hemisphere resort would use e.g. (6, 15) -> (10, 10).
        "season_window": {"start": (12, 1), "end": (4, 20)},
    },
    # --- North America roster (Phase 2) --------------------------------------
    # Each SNOTEL station was chosen as the nearest NRCS SNTL to the resort from
    # the live station metadata (many sit ON the mountain, e.g. "Vail Mountain",
    # "Grand Targhee", "Lone Mountain"=Big Sky); every NWS grid was resolved live
    # via api.weather.gov/points. `verified: False` marks a compromise pairing
    # (station >8 mi away or an elevation mismatch) worth revisiting, not a guess.
    # storm_floor_inches: {24: 8, 72: 15} for deep/maritime climates (Cottonwoods,
    # Tetons, Sierra, PNW), {24: 6, 72: 12} for drier continental ones (CO, MT, NM).
    # NOTE: SNOTEL does not cover the Northeast (0 stations in VT/NH/ME/NY) -- those
    # resorts need a different historical source (see README "Northeast").
    # --- Utah: Snowbird + Park City area (share Alta's SLC office) ---
    "snowbird": {
        "name": "Snowbird, UT",
        "snotel_station": "766:UT:SNTL",
        "snotel_name": "Snowbird",
        "nws_office": "SLC",
        "nws_grid": (107, 166),
        "latitude": 40.581,
        "longitude": -111.6556,
        "verified": True,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (12, 1), "end": (4, 20)},
    },
    "park_city": {
        "name": "Park City, UT",
        "snotel_station": "814:UT:SNTL",
        "snotel_name": "Thaynes Canyon",
        "nws_office": "SLC",
        "nws_grid": (113, 169),
        "latitude": 40.6514,
        "longitude": -111.508,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (12, 1), "end": (4, 20)},
    },
    "deer_valley": {
        "name": "Deer Valley, UT",
        "snotel_station": "814:UT:SNTL",
        "snotel_name": "Thaynes Canyon",
        "nws_office": "SLC",
        "nws_grid": (114, 168),
        "latitude": 40.6374,
        "longitude": -111.4783,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (12, 1), "end": (4, 20)},
    },
    # --- Utah: Big Cottonwood + Wasatch-back ---
    "brighton": {
        "name": "Brighton, UT",
        "snotel_station": "366:UT:SNTL",
        "snotel_name": "Brighton",
        "nws_office": "SLC",
        "nws_grid": (110, 167),
        "latitude": 40.5977,
        "longitude": -111.5836,
        "verified": True,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (12, 1), "end": (4, 20)},
    },
    "solitude": {
        "name": "Solitude, UT",
        "snotel_station": "366:UT:SNTL",
        "snotel_name": "Brighton",
        "nws_office": "SLC",
        "nws_grid": (110, 168),
        "latitude": 40.6199,
        "longitude": -111.5919,
        "verified": True,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (12, 1), "end": (4, 20)},
    },
    "powder_mtn": {
        "name": "Powder Mountain, UT",
        "snotel_station": "1300:UT:SNTL",
        "snotel_name": "Powder Mountain",
        "nws_office": "SLC",
        "nws_grid": (108, 203),
        "latitude": 41.38,
        "longitude": -111.78,
        "verified": True,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (12, 1), "end": (4, 20)},
    },
    # --- Colorado ---
    "vail": {
        "name": "Vail, CO",
        "snotel_station": "842:CO:SNTL",
        "snotel_name": "Vail Mountain",
        "nws_office": "GJT",
        "nws_grid": (174, 119),
        "latitude": 39.6061,
        "longitude": -106.355,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (12, 1), "end": (4, 15)},
    },
    "breckenridge": {
        "name": "Breckenridge, CO",
        "snotel_station": "531:CO:SNTL",
        "snotel_name": "Hoosier Pass",
        "nws_office": "BOU",
        "nws_grid": (26, 53),
        "latitude": 39.4817,
        "longitude": -106.0384,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (12, 1), "end": (4, 15)},
    },
    "aspen": {
        "name": "Aspen Mountain, CO",
        "snotel_station": "1101:CO:SNTL",
        "snotel_name": "Chapman Tunnel",
        "nws_office": "GJT",
        "nws_grid": (156, 102),
        "latitude": 39.1859,
        "longitude": -106.8175,
        "verified": False,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (12, 1), "end": (4, 15)},
    },
    "steamboat": {
        "name": "Steamboat, CO",
        "snotel_station": "457:CO:SNTL",
        "snotel_name": "Dry Lake",
        "nws_office": "GJT",
        "nws_grid": (162, 159),
        "latitude": 40.457,
        "longitude": -106.8045,
        "verified": True,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (12, 1), "end": (4, 15)},
    },
    "winter_park": {
        "name": "Winter Park, CO",
        "snotel_station": "1186:CO:SNTL",
        "snotel_name": "Fool Creek",
        "nws_office": "BOU",
        "nws_grid": (37, 70),
        "latitude": 39.8868,
        "longitude": -105.7625,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (12, 1), "end": (4, 15)},
    },
    "telluride": {
        "name": "Telluride, CO",
        # Alta Lakes (1344) is 3.8 mi but only began 2025; Red Mountain Pass has a
        # 46-yr record at matching alpine elevation, 6 mi south.
        "snotel_station": "713:CO:SNTL",
        "snotel_name": "Red Mountain Pass",
        "nws_office": "GJT",
        "nws_grid": (116, 49),
        "latitude": 37.9375,
        "longitude": -107.8123,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (12, 1), "end": (4, 15)},
    },
    "copper": {
        "name": "Copper Mountain, CO",
        "snotel_station": "415:CO:SNTL",
        "snotel_name": "Copper Mountain",
        "nws_office": "BOU",
        "nws_grid": (22, 54),
        "latitude": 39.5022,
        "longitude": -106.1497,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (12, 1), "end": (4, 15)},
    },
    "keystone": {
        "name": "Keystone, CO",
        "snotel_station": "505:CO:SNTL",
        "snotel_name": "Grizzly Peak",
        "nws_office": "BOU",
        "nws_grid": (29, 58),
        "latitude": 39.6084,
        "longitude": -105.9437,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (12, 1), "end": (4, 15)},
    },
    "arapahoe_basin": {
        "name": "Arapahoe Basin, CO",
        "snotel_station": "505:CO:SNTL",
        "snotel_name": "Grizzly Peak",
        "nws_office": "BOU",
        "nws_grid": (32, 59),
        "latitude": 39.6425,
        "longitude": -105.8719,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (10, 15), "end": (6, 1)},
    },
    # --- Wyoming / Montana / Idaho ---
    "jackson_hole": {
        "name": "Jackson Hole, WY",
        "snotel_station": "689:WY:SNTL",
        "snotel_name": "Phillips Bench",
        "nws_office": "RIW",
        "nws_grid": (40, 144),
        "latitude": 43.5875,
        "longitude": -110.8279,
        "verified": True,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (12, 1), "end": (4, 20)},
    },
    "grand_targhee": {
        "name": "Grand Targhee, WY",
        "snotel_station": "1082:WY:SNTL",
        "snotel_name": "Grand Targhee",
        "nws_office": "RIW",
        "nws_grid": (37, 154),
        "latitude": 43.7871,
        "longitude": -110.9573,
        "verified": True,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (12, 1), "end": (4, 20)},
    },
    "big_sky": {
        "name": "Big Sky, MT",
        "snotel_station": "590:MT:SNTL",
        "snotel_name": "Lone Mountain",
        "nws_office": "TFX",
        "nws_grid": (82, 41),
        "latitude": 45.286,
        "longitude": -111.401,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (12, 1), "end": (4, 20)},
    },
    "bridger_bowl": {
        "name": "Bridger Bowl, MT",
        "snotel_station": "365:MT:SNTL",
        "snotel_name": "Brackett Creek",
        "nws_office": "TFX",
        "nws_grid": (102, 64),
        "latitude": 45.818,
        "longitude": -110.899,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (12, 1), "end": (4, 20)},
    },
    "sun_valley": {
        "name": "Sun Valley, ID",
        "snotel_station": "895:ID:SNTL",
        "snotel_name": "Chocolate Gulch",
        "nws_office": "PIH",
        "nws_grid": (48, 93),
        "latitude": 43.671,
        "longitude": -114.351,
        "verified": False,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (12, 1), "end": (4, 20)},
    },
    "schweitzer": {
        "name": "Schweitzer, ID",
        "snotel_station": "738:ID:SNTL",
        "snotel_name": "Schweitzer Basin",
        "nws_office": "OTX",
        "nws_grid": (171, 119),
        "latitude": 48.369,
        "longitude": -116.623,
        "verified": True,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (11, 20), "end": (5, 10)},
    },
    # --- California: Tahoe / Sierra ---
    "palisades_tahoe": {
        "name": "Palisades Tahoe, CA",
        "snotel_station": "784:CA:SNTL",
        "snotel_name": "Palisades Tahoe",
        "nws_office": "REV",
        "nws_grid": (28, 94),
        "latitude": 39.197,
        "longitude": -120.2357,
        "verified": True,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (11, 25), "end": (4, 25)},
    },
    "heavenly": {
        "name": "Heavenly, CA",
        "snotel_station": "518:CA:SNTL",
        "snotel_name": "Heavenly Valley",
        "nws_office": "REV",
        "nws_grid": (36, 81),
        "latitude": 38.935,
        "longitude": -119.94,
        "verified": True,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (11, 25), "end": (4, 25)},
    },
    "kirkwood": {
        "name": "Kirkwood, CA",
        "snotel_station": "1067:CA:SNTL",
        "snotel_name": "Carson Pass",
        "nws_office": "STO",
        "nws_grid": (91, 63),
        "latitude": 38.6847,
        "longitude": -120.0654,
        "verified": True,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (11, 25), "end": (4, 25)},
    },
    # --- Pacific Northwest ---
    "crystal_mtn": {
        "name": "Crystal Mountain, WA",
        "snotel_station": "642:WA:SNTL",
        "snotel_name": "Morse Lake",
        "nws_office": "SEW",
        "nws_grid": (145, 31),
        "latitude": 46.935,
        "longitude": -121.474,
        "verified": True,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (11, 20), "end": (5, 10)},
    },
    "stevens_pass": {
        "name": "Stevens Pass, WA",
        "snotel_station": "791:WA:SNTL",
        "snotel_name": "Stevens Pass",
        "nws_office": "SEW",
        "nws_grid": (165, 67),
        "latitude": 47.7448,
        "longitude": -121.089,
        "verified": True,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (11, 20), "end": (5, 10)},
    },
    "baker": {
        "name": "Mt Baker, WA",
        "snotel_station": "909:WA:SNTL",
        "snotel_name": "Wells Creek",
        "nws_office": "SEW",
        "nws_grid": (157, 123),
        "latitude": 48.857,
        "longitude": -121.679,
        "verified": True,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (11, 20), "end": (5, 10)},
    },
    "hood_meadows": {
        "name": "Mt Hood Meadows, OR",
        "snotel_station": "651:OR:SNTL",
        "snotel_name": "Mt Hood Test Site",
        "nws_office": "PQR",
        "nws_grid": (144, 88),
        "latitude": 45.331,
        "longitude": -121.665,
        "verified": True,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (11, 20), "end": (5, 10)},
    },
    "bachelor": {
        "name": "Mt Bachelor, OR",
        "snotel_station": "815:OR:SNTL",
        "snotel_name": "Three Creeks Meadow",
        "nws_office": "PDT",
        "nws_grid": (23, 40),
        "latitude": 43.9793,
        "longitude": -121.688,
        "verified": False,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (11, 20), "end": (5, 10)},
    },
    # --- New Mexico ---
    "taos": {
        "name": "Taos Ski Valley, NM",
        "snotel_station": "1168:NM:SNTL",
        "snotel_name": "Taos Powderhorn",
        "nws_office": "ABQ",
        "nws_grid": (147, 185),
        "latitude": 36.596,
        "longitude": -105.447,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (12, 1), "end": (4, 15)},
    },
    # --- Alaska + additional US majors (SNOTEL; stations on-mountain) ---
    "alyeska": {
        "name": "Alyeska, AK",
        "snotel_station": "1103:AK:SNTL",
        "snotel_name": "Mt. Alyeska",
        "nws_office": "AER",
        "nws_grid": (157, 227),
        "latitude": 60.97,
        "longitude": -149.09,
        "verified": True,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (11, 20), "end": (4, 30)},
    },
    "wolf_creek": {
        "name": "Wolf Creek, CO",
        "snotel_station": "874:CO:SNTL",
        "snotel_name": "Wolf Creek Summit",
        "nws_office": "PUB",
        "nws_grid": (15, 36),
        "latitude": 37.4722,
        "longitude": -106.793,
        "verified": True,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (11, 1), "end": (4, 15)},
    },
    "mt_rose": {
        "name": "Mt Rose, NV",
        "snotel_station": "652:NV:SNTL",
        "snotel_name": "Mt Rose Ski Area",
        "nws_office": "REV",
        "nws_grid": (41, 98),
        "latitude": 39.3287,
        "longitude": -119.885,
        "verified": True,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (11, 25), "end": (4, 25)},
    },
    "bogus_basin": {
        "name": "Bogus Basin, ID",
        "snotel_station": "978:ID:SNTL",
        "snotel_name": "Bogus Basin",
        "nws_office": "BOI",
        "nws_grid": (138, 92),
        "latitude": 43.7626,
        "longitude": -116.101,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (12, 1), "end": (4, 1)},
    },
    "brian_head": {
        "name": "Brian Head, UT",
        "snotel_station": "1154:UT:SNTL",
        "snotel_name": "Brian Head",
        "nws_office": "SLC",
        "nws_grid": (49, 42),
        "latitude": 37.7025,
        "longitude": -112.8497,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (12, 1), "end": (4, 10)},
    },
    # --- Eastern Sierra (CDEC; SWE pillows, so swe_gain metric) ---
    "mammoth": {
        "name": "Mammoth Mountain, CA",
        "data_source": "cdec",
        "cdec_station": "MHP",
        "station_name": "Mammoth Pass",
        "nws_office": "REV",
        "nws_grid": (57, 17),
        "latitude": 37.6308,
        "longitude": -119.0326,
        "verified": True,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (11, 1), "end": (5, 31)},
    },
    "june_mountain": {
        "name": "June Mountain, CA",
        "data_source": "cdec",
        "cdec_station": "GEM",
        "station_name": "Gem Pass",
        "nws_office": "REV",
        "nws_grid": (56, 23),
        "latitude": 37.7669,
        "longitude": -119.09,
        "verified": False,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (11, 15), "end": (5, 1)},
    },
    # --- Canada (ECCC/MSC; no SWE -> new_snow metric; NWS is US-only so no
    #     forecast/weather -- these score on history + base). Whistler uses the
    #     on-mountain Roundhouse; Mont-Sainte-Anne station is on-hill. ---
    "whistler_blackcomb": {
        "name": "Whistler Blackcomb, BC",
        "data_source": "eccc",
        "eccc_station": "1108906",
        "station_name": "Whistler Roundhouse",
        "latitude": 50.115,
        "longitude": -122.948,
        "verified": True,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (11, 20), "end": (5, 1)},
    },
    "kicking_horse": {
        "name": "Kicking Horse, BC",
        "data_source": "eccc",
        "eccc_station": "1173210",
        "station_name": "Golden A",
        "latitude": 51.298,
        "longitude": -117.047,
        "verified": False,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (11, 25), "end": (4, 15)},
    },
    "fernie": {
        "name": "Fernie, BC",
        "data_source": "eccc",
        "eccc_station": "1152850",
        "station_name": "Fernie",
        "latitude": 49.463,
        "longitude": -115.091,
        "verified": True,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (11, 25), "end": (4, 15)},
    },
    "sun_peaks": {
        "name": "Sun Peaks, BC",
        "data_source": "eccc",
        "eccc_station": "116C8P0",
        "station_name": "Kamloops Pratt Road",
        "latitude": 50.884,
        "longitude": -119.888,
        "verified": False,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (11, 25), "end": (4, 10)},
    },
    "red_mountain": {
        "name": "Red Mountain, BC",
        "data_source": "eccc",
        "eccc_station": "1141455",
        "station_name": "Castlegar A",
        "latitude": 49.101,
        "longitude": -117.82,
        "verified": False,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (11, 25), "end": (4, 10)},
    },
    "nakiska": {
        "name": "Nakiska, AB",
        "data_source": "eccc",
        "eccc_station": "3053600",
        "station_name": "Kananaskis",
        "latitude": 50.942,
        "longitude": -115.156,
        "verified": True,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (11, 25), "end": (4, 10)},
    },
    "mont_tremblant": {
        "name": "Mont Tremblant, QC",
        "data_source": "eccc",
        "eccc_station": "7033939",
        "station_name": "La Macaza",
        "latitude": 46.21,
        "longitude": -74.585,
        "verified": False,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (12, 1), "end": (4, 5)},
    },
    "mont_sainte_anne": {
        "name": "Mont-Sainte-Anne, QC",
        "data_source": "eccc",
        "eccc_station": "7045326",
        "station_name": "Mont Ste-Anne",
        "latitude": 47.075,
        "longitude": -70.904,
        "verified": True,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (12, 1), "end": (4, 10)},
    },
    "blue_mountain": {
        "name": "Blue Mountain, ON",
        "data_source": "eccc",
        "eccc_station": "6114979",
        "station_name": "Markdale",
        "latitude": 44.501,
        "longitude": -80.311,
        "verified": False,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (12, 15), "end": (3, 25)},
    },
    # --- Canada / BC interior (BC ASWS snow pillows -> SWE, swe_gain metric;
    #     fills the Revelstoke/Silver Star/Big White gap ECCC COOP can't). ---
    "revelstoke": {
        "name": "Revelstoke, BC",
        "data_source": "bcsws",
        "bcsws_station": "2A06P",
        "station_name": "Mount Revelstoke",
        "latitude": 50.958,
        "longitude": -118.163,
        "verified": True,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (11, 25), "end": (4, 20)},
    },
    "silver_star": {
        "name": "Silver Star, BC",
        "data_source": "bcsws",
        "bcsws_station": "2F10P",
        "station_name": "Silver Star Mountain",
        "latitude": 50.3583,
        "longitude": -119.0625,
        "verified": True,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (11, 25), "end": (4, 20)},
    },
    "big_white": {
        "name": "Big White, BC",
        "data_source": "bcsws",
        "bcsws_station": "2F05P",
        "station_name": "Mission Creek",
        "latitude": 49.716,
        "longitude": -118.933,
        "verified": False,
        "storm_floor_inches": {24: 8, 72: 15},
        "season_window": {"start": (11, 25), "end": (4, 20)},
    },
    # --- Northeast (ACIS/COOP source; SNOTEL has no NE stations) -------------
    # No SWE from COOP, so these grade on the "new_snow" metric (config knob:
    # per-mountain `season_metric`). Lower storm floors -- Eastern storms are
    # smaller. Stations chosen for a reliable DAILY snowfall record; Wildcat uses
    # Pinkham Notch (on-mountain, 96 yr). See each entry's notes for compromises.
    "stowe": {
        "name": "Stowe, VT",
        "data_source": "acis",
        # The iconic on-mountain Mount Mansfield stake (USC00435416, 3950 ft) has
        # a superb 70-yr snow-DEPTH record but reports depth only every other day,
        # so a derived new-snow season metric is all gaps. Jeffersonville (3.7 mi,
        # valley) reports daily snowfall, so the season grade is reliable here.
        "acis_sid": "USC00434261",
        "station_name": "Jeffersonville",
        "season_metric": "new_snow",
        "nws_office": "BTV",
        "nws_grid": (102, 61),
        "latitude": 44.5303,
        "longitude": -72.7814,
        "verified": False,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (12, 1), "end": (4, 10)},
    },
    "killington": {
        "name": "Killington, VT",
        "data_source": "acis",
        "acis_sid": "USC00431433",
        "station_name": "Chittenden",
        "season_metric": "new_snow",
        "nws_office": "BTV",
        "nws_grid": (108, 19),
        "latitude": 43.6045,
        "longitude": -72.8201,
        "verified": False,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (12, 1), "end": (4, 10)},
    },
    "sugarbush": {
        "name": "Sugarbush, VT",
        "data_source": "acis",
        "acis_sid": "USC00437612",
        "station_name": "South Lincoln",
        "season_metric": "new_snow",
        "nws_office": "BTV",
        "nws_grid": (101, 42),
        "latitude": 44.136,
        "longitude": -72.9037,
        "verified": False,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (12, 1), "end": (4, 10)},
    },
    "stratton": {
        "name": "Stratton, VT",
        "data_source": "acis",
        "acis_sid": "USC00436335",
        "station_name": "Peru",
        "season_metric": "new_snow",
        "nws_office": "ALY",
        "nws_grid": (97, 89),
        "latitude": 43.1134,
        "longitude": -72.9081,
        "verified": False,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (12, 1), "end": (4, 10)},
    },
    "sugarloaf": {
        "name": "Sugarloaf, ME",
        "data_source": "acis",
        "acis_sid": "USC00174324",
        "station_name": "Kingfield",
        "season_metric": "new_snow",
        "nws_office": "GYX",
        "nws_grid": (59, 120),
        "latitude": 45.0314,
        "longitude": -70.3131,
        "verified": False,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (12, 1), "end": (4, 10)},
    },
    "sunday_river": {
        "name": "Sunday River, ME",
        "data_source": "acis",
        "acis_sid": "USC00170583",
        "station_name": "Bethel 6 SSE",
        "season_metric": "new_snow",
        "nws_office": "GYX",
        "nws_grid": (46, 91),
        "latitude": 44.4735,
        "longitude": -70.8568,
        "verified": False,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (12, 1), "end": (4, 10)},
    },
    "wildcat": {
        "name": "Wildcat, NH",
        "data_source": "acis",
        "acis_sid": "USC00276818",
        "station_name": "Pinkham Notch",
        "season_metric": "new_snow",
        "nws_office": "GYX",
        "nws_grid": (35, 80),
        "latitude": 44.2646,
        "longitude": -71.239,
        "verified": True,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (12, 1), "end": (4, 10)},
    },
    "whiteface": {
        "name": "Whiteface, NY",
        "data_source": "acis",
        "acis_sid": "USC00304555",
        "station_name": "Lake Placid 2 S",
        "season_metric": "new_snow",
        "nws_office": "BTV",
        "nws_grid": (67, 48),
        "latitude": 44.3659,
        "longitude": -73.9026,
        "verified": True,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (12, 1), "end": (4, 10)},
    },
    "hunter": {
        "name": "Hunter Mountain, NY",
        "data_source": "acis",
        # Was Phoenicia 2SW (USC00306570), which ACIS retired 2025-08-31. Repointed
        # to Delhi 2 SE, the nearest active *long-record* COOP (Catskills, elev
        # 1460 ft, 60+ yrs of daily snowfall) -- close active CoCoRaHS stations
        # exist but lack the historical coverage to rank a within-season percentile.
        "acis_sid": "USC00302036",
        "station_name": "Delhi 2 SE",
        "season_metric": "new_snow",
        "nws_office": "ALY",
        "nws_grid": (59, 39),
        "latitude": 42.1779,
        "longitude": -74.2318,
        "verified": False,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (12, 1), "end": (4, 10)},
    },
    # --- Alberta / Banff (Open-Meteo reanalysis; ECCC COOP has no snow here and
    #     the Alberta pillow network has no open historical API). ---
    "lake_louise": {
        "name": "Lake Louise, AB",
        "data_source": "openmeteo",
        "openmeteo_id": "51.4419,-116.1653",
        "latitude": 51.4419,
        "longitude": -116.1653,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (11, 10), "end": (5, 5)},
    },
    "sunshine_village": {
        "name": "Sunshine Village, AB",
        "data_source": "openmeteo",
        "openmeteo_id": "51.0784,-115.7767",
        "latitude": 51.0784,
        "longitude": -115.7767,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (11, 10), "end": (5, 20)},
    },
    "norquay": {
        "name": "Mt Norquay, AB",
        "data_source": "openmeteo",
        "openmeteo_id": "51.2097,-115.5952",
        "latitude": 51.2097,
        "longitude": -115.5952,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (11, 25), "end": (4, 15)},
    },
    "marmot_basin": {
        "name": "Marmot Basin, AB",
        "data_source": "openmeteo",
        "openmeteo_id": "52.7997,-118.0817",
        "latitude": 52.7997,
        "longitude": -118.0817,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (11, 15), "end": (5, 1)},
    },
    # --- Southern Hemisphere (Open-Meteo ERA5 reanalysis; global, no station
    #     network exists here). Modeled snow, so coarse but internally
    #     consistent for percentile grading. water_year auto-starts in May
    #     (latitude < 0) so the Jun-Oct season stays in one accumulation year. ---
    "perisher": {
        "name": "Perisher, AU",
        "data_source": "openmeteo",
        "openmeteo_id": "-36.4058,148.4131",
        "latitude": -36.4058,
        "longitude": 148.4131,
        "verified": True,
        "storm_floor_inches": {24: 3, 72: 6},
        "season_window": {"start": (6, 1), "end": (10, 5)},
    },
    "thredbo": {
        "name": "Thredbo, AU",
        "data_source": "openmeteo",
        "openmeteo_id": "-36.5004,148.3025",
        "latitude": -36.5004,
        "longitude": 148.3025,
        "verified": True,
        "storm_floor_inches": {24: 3, 72: 6},
        "season_window": {"start": (6, 1), "end": (10, 5)},
    },
    "falls_creek": {
        "name": "Falls Creek, AU",
        "data_source": "openmeteo",
        "openmeteo_id": "-36.8656,147.2861",
        "latitude": -36.8656,
        "longitude": 147.2861,
        "verified": True,
        "storm_floor_inches": {24: 3, 72: 6},
        "season_window": {"start": (6, 1), "end": (10, 1)},
    },
    "mt_buller": {
        "name": "Mt Buller, AU",
        "data_source": "openmeteo",
        "openmeteo_id": "-37.1467,146.4473",
        "latitude": -37.1467,
        "longitude": 146.4473,
        "verified": True,
        "storm_floor_inches": {24: 3, 72: 6},
        "season_window": {"start": (6, 1), "end": (9, 25)},
    },
    "mt_hotham": {
        "name": "Mt Hotham, AU",
        "data_source": "openmeteo",
        "openmeteo_id": "-36.9761,147.1358",
        "latitude": -36.9761,
        "longitude": 147.1358,
        "verified": True,
        "storm_floor_inches": {24: 3, 72: 6},
        "season_window": {"start": (6, 1), "end": (10, 1)},
    },
    "coronet_peak": {
        "name": "Coronet Peak, NZ",
        "data_source": "openmeteo",
        "openmeteo_id": "-44.9206,168.735",
        "latitude": -44.9206,
        "longitude": 168.735,
        "verified": True,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (6, 10), "end": (10, 5)},
    },
    "the_remarkables": {
        "name": "The Remarkables, NZ",
        "data_source": "openmeteo",
        "openmeteo_id": "-45.0553,168.8144",
        "latitude": -45.0553,
        "longitude": 168.8144,
        "verified": True,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (6, 10), "end": (10, 10)},
    },
    "cardrona": {
        "name": "Cardrona, NZ",
        "data_source": "openmeteo",
        "openmeteo_id": "-44.8745,168.9481",
        "latitude": -44.8745,
        "longitude": 168.9481,
        "verified": True,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (6, 10), "end": (10, 10)},
    },
    "treble_cone": {
        "name": "Treble Cone, NZ",
        "data_source": "openmeteo",
        "openmeteo_id": "-44.6316,168.8975",
        "latitude": -44.6316,
        "longitude": 168.8975,
        "verified": True,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (6, 15), "end": (10, 1)},
    },
    "mt_hutt": {
        "name": "Mt Hutt, NZ",
        "data_source": "openmeteo",
        "openmeteo_id": "-43.4706,171.5306",
        "latitude": -43.4706,
        "longitude": 171.5306,
        "verified": True,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (6, 10), "end": (10, 10)},
    },
    "whakapapa": {
        "name": "Whakapapa, NZ",
        "data_source": "openmeteo",
        "openmeteo_id": "-39.2546,175.5606",
        "latitude": -39.2546,
        "longitude": 175.5606,
        "verified": True,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (6, 20), "end": (10, 20)},
    },
    "valle_nevado": {
        "name": "Valle Nevado, CL",
        "data_source": "openmeteo",
        "openmeteo_id": "-33.3547,-70.2489",
        "latitude": -33.3547,
        "longitude": -70.2489,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (6, 15), "end": (10, 1)},
    },
    "portillo": {
        "name": "Portillo, CL",
        "data_source": "openmeteo",
        "openmeteo_id": "-32.8353,-70.1281",
        "latitude": -32.8353,
        "longitude": -70.1281,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (6, 15), "end": (10, 1)},
    },
    "nevados_de_chillan": {
        "name": "Nevados de Chillan, CL",
        "data_source": "openmeteo",
        "openmeteo_id": "-36.9058,-71.4064",
        "latitude": -36.9058,
        "longitude": -71.4064,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (6, 15), "end": (10, 5)},
    },
    "cerro_catedral": {
        "name": "Cerro Catedral, AR",
        "data_source": "openmeteo",
        "openmeteo_id": "-41.1672,-71.4406",
        "latitude": -41.1672,
        "longitude": -71.4406,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (6, 15), "end": (10, 1)},
    },
    "las_lenas": {
        "name": "Las Lenas, AR",
        "data_source": "openmeteo",
        "openmeteo_id": "-35.1497,-70.0781",
        "latitude": -35.1497,
        "longitude": -70.0781,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (6, 20), "end": (9, 30)},
    },
    # --- Europe (Open-Meteo ERA5 reanalysis, same rationale as the Southern
    #     Hemisphere block: no single open station network spans the continent,
    #     and percentile grading only needs a long internally-consistent record).
    #     Leaf regions are mountain RANGES (Alps, Pyrenees, ...), not countries:
    #     France and Italy each straddle two ranges, so the ambiguous resorts set
    #     an explicit "region" override instead of relying on the country-code
    #     parse (see ski/regions.py). ---
    # Alps -- France
    "chamonix": {
        "name": "Chamonix, FR",
        "data_source": "openmeteo",
        "openmeteo_id": "45.9237,6.8694",
        "latitude": 45.9237,
        "longitude": 6.8694,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (12, 1), "end": (5, 1)},
    },
    "val_disere": {
        "name": "Val d'Isere, FR",
        "data_source": "openmeteo",
        "openmeteo_id": "45.4489,6.9797",
        "latitude": 45.4489,
        "longitude": 6.9797,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (11, 25), "end": (5, 1)},
    },
    "val_thorens": {
        "name": "Val Thorens, FR",
        "data_source": "openmeteo",
        "openmeteo_id": "45.2979,6.58",
        "latitude": 45.2979,
        "longitude": 6.58,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (11, 20), "end": (5, 5)},
    },
    "alpe_dhuez": {
        "name": "Alpe d'Huez, FR",
        "data_source": "openmeteo",
        "openmeteo_id": "45.091,6.0686",
        "latitude": 45.091,
        "longitude": 6.0686,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (12, 1), "end": (4, 25)},
    },
    "la_plagne": {
        "name": "La Plagne, FR",
        "data_source": "openmeteo",
        "openmeteo_id": "45.5062,6.6786",
        "latitude": 45.5062,
        "longitude": 6.6786,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (12, 1), "end": (4, 25)},
    },
    # Alps -- Switzerland
    "zermatt": {
        "name": "Zermatt, CH",
        "data_source": "openmeteo",
        "openmeteo_id": "46.0207,7.7491",
        "latitude": 46.0207,
        "longitude": 7.7491,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (11, 15), "end": (5, 1)},
    },
    "verbier": {
        "name": "Verbier, CH",
        "data_source": "openmeteo",
        "openmeteo_id": "46.0964,7.2281",
        "latitude": 46.0964,
        "longitude": 7.2281,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (11, 25), "end": (4, 25)},
    },
    "st_moritz": {
        "name": "St. Moritz, CH",
        "data_source": "openmeteo",
        "openmeteo_id": "46.4908,9.8355",
        "latitude": 46.4908,
        "longitude": 9.8355,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (11, 25), "end": (4, 20)},
    },
    "engelberg": {
        "name": "Engelberg, CH",
        "data_source": "openmeteo",
        "openmeteo_id": "46.8205,8.4013",
        "latitude": 46.8205,
        "longitude": 8.4013,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (11, 20), "end": (5, 10)},
    },
    "davos": {
        "name": "Davos, CH",
        "data_source": "openmeteo",
        "openmeteo_id": "46.8043,9.837",
        "latitude": 46.8043,
        "longitude": 9.837,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (11, 25), "end": (4, 15)},
    },
    # Alps -- Austria
    "st_anton": {
        "name": "St. Anton, AT",
        "data_source": "openmeteo",
        "openmeteo_id": "47.1287,10.2643",
        "latitude": 47.1287,
        "longitude": 10.2643,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (12, 1), "end": (4, 25)},
    },
    "kitzbuehel": {
        "name": "Kitzbuhel, AT",
        "data_source": "openmeteo",
        "openmeteo_id": "47.4467,12.3925",
        "latitude": 47.4467,
        "longitude": 12.3925,
        "verified": True,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (11, 20), "end": (4, 10)},
    },
    "soelden": {
        "name": "Solden, AT",
        "data_source": "openmeteo",
        "openmeteo_id": "46.9655,11.0079",
        "latitude": 46.9655,
        "longitude": 11.0079,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (11, 10), "end": (5, 5)},
    },
    "ischgl": {
        "name": "Ischgl, AT",
        "data_source": "openmeteo",
        "openmeteo_id": "47.0126,10.2913",
        "latitude": 47.0126,
        "longitude": 10.2913,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (11, 25), "end": (5, 1)},
    },
    # Alps -- Italy (western; the Dolomites resorts below override their region)
    "cervinia": {
        "name": "Cervinia, IT",
        "data_source": "openmeteo",
        "openmeteo_id": "45.9365,7.63",
        "latitude": 45.9365,
        "longitude": 7.63,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (11, 10), "end": (5, 1)},
    },
    "sestriere": {
        "name": "Sestriere, IT",
        "data_source": "openmeteo",
        "openmeteo_id": "44.9565,6.879",
        "latitude": 44.9565,
        "longitude": 6.879,
        "verified": True,
        "storm_floor_inches": {24: 6, 72: 12},
        "season_window": {"start": (12, 1), "end": (4, 15)},
    },
    # Alps -- Germany / Slovenia
    "zugspitze": {
        "name": "Zugspitze (Garmisch), DE",
        "data_source": "openmeteo",
        "openmeteo_id": "47.4212,10.9863",
        "latitude": 47.4212,
        "longitude": 10.9863,
        "verified": True,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (11, 20), "end": (5, 1)},
    },
    "kranjska_gora": {
        "name": "Kranjska Gora, SI",
        "data_source": "openmeteo",
        "openmeteo_id": "46.4846,13.7837",
        "latitude": 46.4846,
        "longitude": 13.7837,
        "verified": True,
        "storm_floor_inches": {24: 3, 72: 6},
        "season_window": {"start": (12, 10), "end": (3, 31)},
    },
    "cortina": {
        "name": "Cortina d'Ampezzo, IT",
        "data_source": "openmeteo",
        "openmeteo_id": "46.5405,12.1357",
        "latitude": 46.5405,
        "longitude": 12.1357,
        "verified": True,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (11, 25), "end": (4, 15)},
    },
    "val_gardena": {
        "name": "Val Gardena, IT",
        "data_source": "openmeteo",
        "openmeteo_id": "46.556,11.762",
        "latitude": 46.556,
        "longitude": 11.762,
        "verified": True,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (12, 1), "end": (4, 10)},
    },
    "baqueira_beret": {
        "name": "Baqueira-Beret, ES",
        "data_source": "openmeteo",
        "openmeteo_id": "42.6986,0.9311",
        "latitude": 42.6986,
        "longitude": 0.9311,
        "verified": True,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (12, 1), "end": (4, 15)},
    },
    "formigal": {
        "name": "Formigal, ES",
        "data_source": "openmeteo",
        "openmeteo_id": "42.7772,-0.3814",
        "latitude": 42.7772,
        "longitude": -0.3814,
        "verified": True,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (12, 1), "end": (4, 10)},
    },
    "grandvalira": {
        "name": "Grandvalira, AD",
        "data_source": "openmeteo",
        "openmeteo_id": "42.543,1.7333",
        "latitude": 42.543,
        "longitude": 1.7333,
        "verified": True,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (12, 1), "end": (4, 15)},
    },
    "saint_lary": {
        "name": "Saint-Lary-Soulan, FR",
        "data_source": "openmeteo",
        "openmeteo_id": "42.8129,0.3218",
        "latitude": 42.8129,
        "longitude": 0.3218,
        "verified": True,
        "storm_floor_inches": {24: 4, 72: 8},
        "season_window": {"start": (12, 5), "end": (4, 5)},
    },
    # Scandinavia
    "are": {
        "name": "Are, SE",
        "data_source": "openmeteo",
        "openmeteo_id": "63.399,13.0815",
        "latitude": 63.399,
        "longitude": 13.0815,
        "verified": True,
        "storm_floor_inches": {24: 3, 72: 6},
        "season_window": {"start": (11, 20), "end": (5, 1)},
    },
    "trysil": {
        "name": "Trysil, NO",
        "data_source": "openmeteo",
        "openmeteo_id": "61.2926,12.2827",
        "latitude": 61.2926,
        "longitude": 12.2827,
        "verified": True,
        "storm_floor_inches": {24: 3, 72: 6},
        "season_window": {"start": (11, 15), "end": (4, 20)},
    },
    "hemsedal": {
        "name": "Hemsedal, NO",
        "data_source": "openmeteo",
        "openmeteo_id": "60.8631,8.551",
        "latitude": 60.8631,
        "longitude": 8.551,
        "verified": True,
        "storm_floor_inches": {24: 3, 72: 6},
        "season_window": {"start": (11, 15), "end": (5, 1)},
    },
    "levi": {
        "name": "Levi, FI",
        "data_source": "openmeteo",
        "openmeteo_id": "67.805,24.809",
        "latitude": 67.805,
        "longitude": 24.809,
        "verified": True,
        "storm_floor_inches": {24: 3, 72: 6},
        "season_window": {"start": (11, 1), "end": (5, 5)},
    },
    "ruka": {
        "name": "Ruka, FI",
        "data_source": "openmeteo",
        "openmeteo_id": "66.165,29.1417",
        "latitude": 66.165,
        "longitude": 29.1417,
        "verified": True,
        "storm_floor_inches": {24: 3, 72: 6},
        "season_window": {"start": (11, 1), "end": (5, 10)},
    },
    # Carpathians
    "poiana_brasov": {
        "name": "Poiana Brasov, RO",
        "data_source": "openmeteo",
        "openmeteo_id": "45.5959,25.5559",
        "latitude": 45.5959,
        "longitude": 25.5559,
        "verified": True,
        "storm_floor_inches": {24: 3, 72: 6},
        "season_window": {"start": (12, 10), "end": (3, 31)},
    },
    "jasna": {
        "name": "Jasna, SK",
        "data_source": "openmeteo",
        "openmeteo_id": "48.9634,19.5836",
        "latitude": 48.9634,
        "longitude": 19.5836,
        "verified": True,
        "storm_floor_inches": {24: 3, 72: 6},
        "season_window": {"start": (12, 5), "end": (4, 10)},
    },
    "kasprowy_wierch": {
        "name": "Kasprowy Wierch, PL",
        "data_source": "openmeteo",
        "openmeteo_id": "49.2317,19.9817",
        "latitude": 49.2317,
        "longitude": 19.9817,
        "verified": True,
        "storm_floor_inches": {24: 3, 72: 6},
        "season_window": {"start": (12, 1), "end": (4, 25)},
    },
    # Balkans
    "bansko": {
        "name": "Bansko, BG",
        "data_source": "openmeteo",
        "openmeteo_id": "41.79,23.45",
        "latitude": 41.79,
        "longitude": 23.45,
        "verified": True,
        "storm_floor_inches": {24: 3, 72: 6},
        "season_window": {"start": (12, 15), "end": (4, 10)},
    },
    # Scotland
    "cairngorm": {
        "name": "Cairngorm Mountain, GB",
        "data_source": "openmeteo",
        "openmeteo_id": "57.133,-3.644",
        "latitude": 57.133,
        "longitude": -3.644,
        "verified": True,
        "storm_floor_inches": {24: 2, 72: 4},
        "season_window": {"start": (12, 15), "end": (4, 15)},
    },
}

# ---------------------------------------------------------------------------
# Percentile -> letter grade
# ---------------------------------------------------------------------------
# Ordered high -> low. A grade is the first tier whose `min_percentile` the
# value meets or exceeds. Keep the list sorted high->low.
#
# Tuned against 37 yrs of Snowbird SWE-gain (see docs/tuning.md): granular at the
# top (A+ is reserved for ~top 5%, e.g. WY2011/2005), single-letter grades, and a
# bottom band where ~14% of years land D/F (a genuinely bad Utah season -- 2015,
# 2018 -- not merely below average). Median season grades B-.
GRADE_THRESHOLDS = [
    (96, "A+"),
    (88, "A"),
    (80, "A-"),
    (70, "B+"),
    (58, "B"),
    (46, "B-"),
    (34, "C+"),
    (24, "C"),
    (16, "C-"),
    (11, "D"),
    (0,  "F"),
]

# The OVERALL score's letter curve. Deliberately separate from GRADE_THRESHOLDS:
# that curve maps raw PERCENTILES (uniform 0-100 by construction), while the
# overall value is a strict power-mean of mixed sub-scores scaled by the cover
# gate, so its distribution sits much lower -- borrowing the percentile curve
# graded a median mountain in a median week C/D. Calibrated empirically (see
# docs/tuning.md "Overall letter curve"): overall values backtested across the
# full 79-mountain roster x ~15 seasons, 3 in-season dates each (n=3494, median
# value 33, p90 74), cutoffs placed at the same cumulative fractions the
# percentile curve targets (A+ ~ top 4%, median ~ B-, D/F ~ bottom 14%).
OVERALL_GRADE_THRESHOLDS = [
    (85, "A+"),   # p96 of backtested overall values
    (71, "A"),    # p88
    (60, "A-"),   # p80
    (50, "B+"),   # p70
    (39, "B"),    # p58
    (30, "B-"),   # p46  (median season-week ~33 -> B-)
    (23, "C+"),   # p34
    (17, "C"),    # p24
    (13, "C-"),   # p16
    (9,  "D"),    # p11
    (0,  "F"),
]

# ---------------------------------------------------------------------------
# Storm alerting (Phase 2) -- kept separate from grading on purpose.
# ---------------------------------------------------------------------------
# Two baselines on purpose (the raw data forces the distinction):
#   * The storm LETTER GRADE ranks a total against past MEANINGFUL-SNOW windows
#     (>= grade_baseline_min_inches). Ranking against every window incl. dry days
#     saturates -- median 24hr snowfall is 0", so any real storm would read ~100th.
#   * The ALERT ranks against ALL windows (so the absolute floor stays the binding
#     constraint) and additionally requires the floor -- a top-percentile dusting
#     in a historically dry micro-window shouldn't page you.
STORM_THRESHOLDS = {
    "windows_hours": [24, 72],
    "min_inches": {24: 8, 72: 15},     # DEFAULT absolute floor (a "good day no matter what")
    "min_percentile": 90,              # AND top-decile of ALL windows
    "grade_baseline_min_inches": 4,    # "counts as a storm" floor for the LETTER grade
}

# The alert floor is not one fixed number -- 6" means different things by climate
# and by year. Two adjustments layer on top of the default above:
#   1. Per mountain: set `storm_floor_inches` in a MOUNTAINS entry (e.g. an East
#      Coast hill would use a much lower floor than Little Cottonwood).
#   2. Per season (year-relative): in a lean year you're hungry for any snow, so
#      the floor drops; in a fat year small storms are routine, so it can rise.
#      Driven by the current season-to-date percentile.
STORM_ALERT_SEASON_SCALING = {
    "lean_below_pct": 35, "lean_factor": 0.6,   # poor season -> lower the bar (8" -> ~4.8")
    "fat_above_pct": 80,  "fat_factor": 1.0,    # great season -> raise it (1.0 = off; tune up to cut powder-year noise)
}

# ---------------------------------------------------------------------------
# Data-quality knobs
# ---------------------------------------------------------------------------
# Fewer than this many usable historical years -> still show the grade, but flag
# it low-confidence rather than hiding it.
LOW_CONFIDENCE_YEARS = 10

# A historical year only counts toward the season-to-date distribution if it has
# at least this fraction of the days present in [day 1 .. today's day-of-season].
# We skip sparse years rather than interpolating -- interpolation fakes signal.
SEASON_COVERAGE_MIN = 0.90

# Which daily quantity to cumulate for the season-to-date grade:
#   "swe_gain"  -- sum of positive daily SWE (WTEQ) increments. Physically the
#                 snow water accumulated so far. PREFERRED: full station record
#                 (Snowbird has SWE back to 1989 but snow depth only from ~2003).
#   "new_snow"  -- sum of derived new_snow_24hr (positive snow-depth change).
#                 Intuitive inches, but shorter history and undercounts settling.
SEASON_METRIC = "swe_gain"

# ---------------------------------------------------------------------------
# Overall mountain score
# ---------------------------------------------------------------------------
# The overall score is a weighted blend of four 0-100 sub-scores:
#   season     -- season-to-date grade (whole winter so far)
#   in_season  -- rolling-30-day "hot month" grade (recent momentum)
#   forecast   -- incoming snow (NEUTRAL 50 when dry; only ever a boost)
#   conditions -- current base depth + weather quality (how it skis right now)
#
# Pick the profile that matches the question you're asking. Weights need not sum
# to 100; they're normalized over whichever sub-scores are available.
# Manual toggles, each matching a decision horizon:
#   weekend -> next few days: what's on the ground + what's imminently incoming
#   month   -> next ~6-8 weeks: base + season quality dominate; day's weather and
#              the 7-day forecast barely inform a 6-week window
#   season  -> the rest of the season: whole-winter quality dominates
# The default is `dynamic`, which auto-blends by how far into the season it is
# (see DYNAMIC_WEIGHTS) -- the three manual toggles are there for when you
# specifically want that lens.
SCORE_PROFILES = {
    "weekend": {"conditions": 40, "forecast": 35, "in_season": 20, "season": 5},
    "month":   {"season": 35, "conditions": 30, "in_season": 25, "forecast": 10},
    "season":  {"season": 55, "in_season": 20, "conditions": 20, "forecast": 5},
}
DEFAULT_PROFILE = "dynamic"

# `dynamic` interpolates between these two weight sets by season progress (0 at
# the season's start, 1 at its end). Early on there's time for the season to play
# out, so season-history and forecast lead; late, it's now-or-never, so current
# conditions lead. Progress is derived from each mountain's own `season_window`,
# so this works unchanged in the Southern Hemisphere or any calendar.
DYNAMIC_WEIGHTS = {
    "start": {"season": 45, "in_season": 20, "forecast": 20, "conditions": 15},
    "end":   {"season": 10, "in_season": 20, "forecast": 15, "conditions": 55},
}

# How hard weak sub-scores drag the overall down. 1.0 = plain average; lower =
# stricter on the worst component (a great season can't hide bad conditions).
# 0.5 makes bad cases bite while leaving good cases ~unchanged.
SCORE_BLEND_EXPONENT = 0.5

# Forecast downside ("thaw") -- a forecast is not only ever good news. Incoming
# rain or a sustained warm spell destroys the base, so it must score BELOW
# neutral rather than dropping out like a benign dry forecast. The index ramps
# 0..1 (see score.thaw_index):
#   rain: 72h liquid rain, no penalty below rain_zero_in, full at rain_full_in.
#         Rain-on-snow is the primary base-killer, so rain alone can reach 1.0.
#         Absolute + season-blind: rain-on-snow is bad in any month.
#   warmth: 72h max temperature, ramping warm_zero_f..warm_full_f, then scaled
#         by warm_weight -- warm+sunny melts slower than rain, so warmth alone
#         caps at half a full penalty. SEASON-TAPERED (warm_taper_by_progress):
#         warmth is only a threat when it's anomalous. A 45F day in January is a
#         genuine melt event on a base you need for months; the same day in April
#         is just spring at a resort whose product IS warm corn. The warmth
#         weight fades to (1 - warm_taper_by_progress) of full by season's end,
#         keeping a residual so an extreme late-season heat wave still bites.
FORECAST_THAW = {
    "rain_zero_in": 0.10,          # <= this much 72h rain: ignored (drizzle/noise)
    "rain_full_in": 1.00,          # this much rain-on-snow = full thaw penalty
    "warm_zero_f": 40,             # 72h max temp at/below this: no warmth penalty
    "warm_full_f": 55,             # sustained warmth this high = max warmth penalty
    "warm_weight": 0.5,            # warmth alone counts at most half of a full thaw
    "warm_taper_by_progress": 0.8, # late season, warmth counts (1 - 0.8*progress)
}

# Recent refreeze ("crust") -- the BACKWARD mirror of thaw, on the conditions
# side. A thaw the forecast already saw coming vanishes from the score the next
# day, exactly when the surface is worst: rain/melt that has since refrozen is
# boilerplate/ice TODAY. This looks at trailing actuals (score.refreeze_index):
#   thaw_happened: recent 72h rain OR warmth (max of the two ramps) -- was there
#       a melt event? warmth here is NOT season-tapered: a refrozen crust skis
#       like a rink in April as much as in January (unlike the forward warmth
#       threat, which spring makes moot).
#   refroze: coldest temp in the last 24h -- froze_zero_f (no real freeze) down
#       to froze_full_f (hard freeze locks the crust in).
#   heal: fresh snow resurfaces the crust, so the penalty decays as the trailing
#       7-day new-snow total rises (this is WHY fresh snow matters here).
# The result multiplies the conditions sub-score down by at most max_penalty.
REFREEZE = {
    "rain_zero_in": 0.10, "rain_full_in": 0.75,   # recent rain that counts as a thaw
    "warm_zero_f": 36,    "warm_full_f": 50,       # recent warmth that counts as a thaw
    "froze_zero_f": 32,   "froze_full_f": 22,      # tmin: 32 = no freeze, 22 = hard freeze
    "heal_zero_in": 2,    "heal_full_in": 10,      # fresh snow that resurfaces the crust
    "max_penalty": 0.40,                            # an icy day caps conditions at 60%
}

# Conditions sub-score -- "how does it ski RIGHT NOW". Blends the RELATIVE base
# signal (percentile vs this mountain's own history: is it at its best?) with
# ABSOLUTE fresh snow and live weather. Absolute base DEPTH is handled solely by
# the multiplicative cover gate (COVER_GATE below) -- it used to sit here too
# (`base_abs`), which triple-counted thin cover (base_rel + base_abs + gate).
CONDITIONS = {
    # weights are renormalized over whichever parts are available
    "mix": {
        "base_rel": 0.40,   # depth percentile vs own history (relative)
        "fresh": 0.35,      # absolute 7-day new snow (the "right now" signal)
        "weather": 0.25,    # live temp/wind/sky (NWS or Open-Meteo)
    },
    "ideal_temp_f": (15, 30),     # full marks in this band
    "temp_warm_zero_f": 48,       # score hits 0 at/above this (slush)
    "temp_cold_floor_f": -5,      # very cold: unpleasant but still skiable
    "temp_cold_score": 45,        # floor score for deep cold
    "wind_zero_mph": 35,          # score hits 0 at/above this
    "weather_mix": {"temp": 0.50, "wind": 0.35, "sky": 0.15},
}

# Absolute base-depth curve: (inches, score) knots, linear between, clamped at
# the ends. Rough industry intuition: <12" marginal cover, ~24" most terrain
# open, 40"+ solid mid-winter, 60"+ everything's buried.
DEPTH_SCORE_CURVE = [(0, 0), (12, 25), (24, 50), (40, 75), (60, 92), (80, 100)]

# Absolute fresh-snow curve over the trailing 7 days: 0" is a groomer week (not
# terrible -> a real baseline, not zero), 6" is a refresh, 12"+ is a powder week.
FRESH_SCORE_CURVE = [(0, 35), (3, 55), (6, 70), (12, 88), (18, 100)]

# The trailing window (days) for the fresh-snow signal.
FRESH_WINDOW_DAYS = 7

# ---------------------------------------------------------------------------
# Absolute Skiability -- the honest "how good is the skiing here, right now"
# ---------------------------------------------------------------------------
# `overall` (score.overall_score) is SELF-RELATIVE: percentiles vs. this
# mountain's own history, so its best-season-ever can outrank an ordinary one.
# `global_score`/`regional_score` (ski.comparable) are a cross-mountain RANK, so
# in a league-wide bad year the "best" hill still ranks ~100th -> A+. Neither
# answers, in absolute terms, "is the skiing actually good here today?".
#
# skiability_score does. It's built from ABSOLUTE inches only -- no percentiles,
# no ranking -- so a number means the same thing at Alta and at a molehill, and
# it governs the headline grade in BOTH directions: bad conditions can't read
# high off a #1 ranking (cap), great conditions can't be buried by a crowded
# leaderboard (floor). Three factors, each a different role:
#
#   base   -- the ENABLER: coverage decides what's open/safe/smooth, but a deep
#             base with no new snow is solid, not epic. Reuses DEPTH_SCORE_CURVE
#             (saturating), contributes up to SKI_BASE_MAX -- so base alone tops
#             out around B-; powder is required to reach the A range.
#   powder -- the DRIVER: recency-weighted fresh + horizon-discounted forecast,
#             on POWDER_SCORE_CURVE (diminishing returns so ~24" is a near-full
#             bump, not infinite). Contributes up to SKI_POWDER_MAX and can push
#             a good-base day to A+.
#   quality-- the PUNISHER: rain/refreeze/thaw and poor weather scale the whole
#             thing down (multiplicative, floored at SKI_QUALITY_FLOOR). Mostly
#             it can only hurt -- an icy or raining day isn't a good day.
SKI_BASE_MAX = 50.0        # max points from settled base depth (the enabler)
SKI_POWDER_MAX = 60.0      # max points from powder (fresh + incoming; the driver)

# Recency/horizon weights folding fresh + forecast into "effective powder inches".
# Snow on the ground this instant is worth most; older fresh has been skied off /
# settled; imminent forecast powder is nearly as good as on the ground and a real
# reason to go (per the "24 inches incoming is a huge driver" intent).
SKI_POWDER_WEIGHTS = {
    "recent": 1.00,    # trailing COMPARABLE_FRESH_WINDOW_DAYS (~72h) of new snow
    "week": 0.40,      # the rest of the trailing-7d total (days ~3-7, older)
    "forecast": 0.85,  # next-72h phase-adjusted expected snowfall (incoming)
}

# Effective-powder inches -> 0-100. Diminishing returns: a refresh matters, a
# big storm matters a lot, but 36"+ is not linearly better than 24" (you can
# only ski so much powder). ~24" lands near the top -> nearly a full letter.
POWDER_SCORE_CURVE = [(0, 0), (3, 22), (6, 38), (12, 62), (18, 80), (24, 90), (36, 100)]

# ---------------------------------------------------------------------------
# Powder recency decay (Phase 5) -- a great storm yesterday != the same storm 10
# days ago, and cold snow keeps far longer than snow that's since seen a thaw.
# ---------------------------------------------------------------------------
# Replaces the old recent(72h)/week(3-7d)/hard-7-day-cliff step for the skiability
# powder input: each day's new snow decays continuously by age (half-life below),
# and the decay ACCELERATES when the pack has seen a recent melt-freeze (the
# refreeze index) -- cold, dry snow is preserved; a thaw skis the old powder off
# and crusts what's left. Contained to the skiability headline; the comparable
# "fresh" input and the conditions "fresh" sub-score keep their fixed windows.
POWDER_DECAY = {
    "half_life_days_cold": 4.0,  # cold, undisturbed powder: half its value in 4 days
    "min_half_life_days": 1.3,   # a full melt-freeze collapses it this fast
    "melt_accel": 1.6,           # how hard refreeze shortens the half-life
    "max_age_days": 12,          # how far back to look (beyond this, negligible)
}

# ---------------------------------------------------------------------------
# Trip Predictor (future-date ranking) -- the SAME exponential decay curve as the
# powder recency decay above, applied to the INVERSE axis.
# ---------------------------------------------------------------------------
# The recency decay weights an OBSERVATION down as it AGES; the trip blend weights
# TODAY'S CONDITIONS down as the trip date RECEDES. Same kernel (score.decay_weight,
# 0.5 ** (x / half_life)), two axes -- x = observation age there, x = lead time here.
#
# TripScore = w * current_comparable_score + (1 - w) * historical_baseline_score,
# with w = decay_weight(lead_days, half_life_days), blended over whichever terms
# are present (ski.trip.blend_trip_score). At lead 0, w = 1 -> the trip score IS
# today's global/regional score (converges to live scoring); months out, w ~ 0 ->
# the ranking leans entirely on history. Half-life 5d: current conditions are ~25%
# of the blend at 10 days out, ~14% at 14 -- the outer edge of real forecast skill.
TRIP_LEAD_DECAY = {
    "half_life_days": 5.0,   # weight on today's conditions halves every 5 lead days
    "max_lead_days": 366,    # how far out the picker/endpoint will predict
}

# The historical baseline aggregates each mountain's conditions over a +/- window
# of the target date's day-of-water-year, across ALL years (ski.trip.climatology).
# Wider = smoother/more years per estimate but blurs the seasonal curve.
TRIP_WINDOW_DAYS = 7

# The baseline is a comparable score (ski.comparable.score_population) just like the
# live global_score, but with ITS OWN weights: for a trip months out you can't catch
# a specific storm, so the persistent PACK signals (typical base depth, cumulative
# season total by that date) lead, and the transient `fresh` window is demoted (not
# dropped -- averaged over decades it still separates a reliably-snowy-in-March
# climate from one that's tapering off). `forecast` has no meaning in a historical
# window and is omitted; `quality` is reserved for when a historical density/wind
# proxy exists (the live density read has no multi-decade analog yet). Weights need
# not sum to 1 -- score_population renormalizes over whatever's present.
TRIP_BASELINE_WEIGHTS = {"base": 0.40, "season": 0.35, "fresh": 0.20, "quality": 0.05}

# Quality multiplier on (base + powder). Weather shaves up to (1 - weather_span);
# refreeze (icy crust) and thaw (incoming rain/warmth) each apply their own
# penalty. Floored so even a raining day keeps a sortable, non-zero number.
SKI_QUALITY = {
    "weather_span": 0.25,     # weather_q=0 -> ×0.75, weather_q=100 -> ×1.0
    "refreeze_penalty": 0.35, # full refrozen crust -> ×0.65
    "thaw_penalty": 0.30,     # full incoming thaw  -> ×0.70
    "wind_penalty": 0.25,     # fully wind-hammered fresh snow -> ×0.75 (Phase 3)
    # Lowered 0.35 -> 0.22 in Phase 4: with density + wind now stacking onto crust
    # + thaw + weather, a genuinely awful day (deep base, big totals, but rained-on,
    # refrozen, and wind-scoured) SHOULD be able to grade down into D/F instead of
    # being propped up at C by a floor tuned when quality could barely move. Still
    # non-zero so off-days stay sortable. Re-tune thresholds after this (Phase 6).
    "floor": 0.22,
}

# ---------------------------------------------------------------------------
# Wind loading / scour (Phase 3) -- sustained wind strips or slabs fresh snow
# ---------------------------------------------------------------------------
# Magnitude-only for now (direction -> aspect is deferred with B7): sustained
# wind takes soft new snow and either scours it off the exposed slopes or packs
# it into wind slab, so a big storm can ski far worse than its totals suggest.
# The signal is the SUSTAINED recent wind (a high hourly quantile over the last
# 72h, not a single gust), fetched from Open-Meteo hourly for every mountain via
# the same recent-conditions path density uses.
WIND = {
    "sustained_quantile": 0.9,  # which hourly-wind quantile counts as "sustained"
    "calm_mph": 12.0,           # at/below -> no scour (0 severity)
    "gale_mph": 38.0,           # at/above -> full severity (1.0)
    # Wind mostly matters when there's loose new snow to move. With fresh snow the
    # penalty is full; with none it still applies at `no_fresh_weight` (wind can
    # still scour/harden an old surface on exposed terrain, just less dramatically).
    "fresh_gate_zero_in": 0.0,
    "fresh_gate_full_in": 6.0,
    "no_fresh_weight": 0.35,
}

# ---------------------------------------------------------------------------
# Snow Quality Score (0-100) -- explainable skiability signal (Phase 0 scaffold)
# ---------------------------------------------------------------------------
# One named, debuggable number for how good the SURFACE is (as opposed to how
# MUCH snow there is). Higher = better. Built from component sub-scores, each
# 0-100, blended over whichever are AVAILABLE -- a missing component renormalizes
# out rather than counting as zero, the same convention as score.conditions_score
# / score.overall_score. `density` and `wind` are placeholders here: they always
# read None until Phases 2/3 wire real data in, so they contribute nothing yet.
#
# SCAFFOLD ONLY (Phase 0): this number is surfaced on the card for observation but
# is weighted 0 in every consumer -- the skiability quality_factor and the
# comparable leaderboard are unchanged -- so it moves no grade. It exists so we can
# eyeball real values across the roster before making them load-bearing in Phase 4.
# See docs/snow-quality-plan.md.
SNOW_QUALITY_WEIGHTS = {
    "density": 0.28,   # SWE:depth -- light/dry vs. heavy/dense (Phase 2)
    "wind":    0.22,   # loading / scour of fresh snow (Phase 3; inert now)
    "crust":   0.20,   # refrozen melt crust (from score.refreeze_index)
    "thaw":    0.18,   # incoming rain/warmth (from score.thaw_index)
    "warmth":  0.12,   # live weather comfort (from score.weather_quality)
}

# ---------------------------------------------------------------------------
# New-snow density (Phase 2) -- "12 inches of blower vs. 12 inches of cement"
# ---------------------------------------------------------------------------
# The density of the RECENT storm's snow (water fraction = SWE gain / depth gain),
# the single clearest quality lever: light, dry snow skis far better than dense,
# wet snow of the same depth. Absolute, not relative -- heavy snow skis heavy at
# every mountain (user decision, 2026-07-17). Two tiers feed one ratio:
#   Tier 1 (measured): SWE-gain / depth-gain from stored obs, where BOTH SWE and
#     depth exist -- the 36 SNOTEL stations + Mammoth (verified 2026-07-17).
#   Tier 2 (derived):  a snowfall-weighted recent temperature, mapped to a ratio
#     via DENSITY_FROM_TEMP, for everyone else (ACIS/ECCC/BCSWS/Open-Meteo). Uses
#     only fields already fetched (snowfall + temperature_2m) -- no unit-ambiguous
#     precip math, no new API params.
# The ratio then maps to (a) a 0-100 quality sub-score for SNOW_QUALITY_WEIGHTS
# and (b) a gentle multiplier on recent powder inches (a dense inch counts for
# less), which is the only place density moves a GRADE in Phase 2 -- via
# score.effective_powder_in -> skiability. Density does NOT enter the comparable
# leaderboard until Phase 4. See docs/snow-quality-plan.md.

# Minimum recent new snow (inches) before a density reading is trusted; below it
# there's too little accumulation to tell light from heavy, so density -> None
# (the component drops out, neutral, rather than reading noise as a verdict).
DENSITY_MIN_SNOW_IN = 2.0

# ---------------------------------------------------------------------------
# Buried rain/melt crust persistence (Phase 5b) -- SNOTEL/pillow stations only
# ---------------------------------------------------------------------------
# The trailing refreeze index only sees the last 72h, but a mid-season rain-on-snow
# event leaves an ice layer that keeps skiing firm for WEEKS until enough snow
# buries it deeply. We can detect the event from the stored pillow record without
# any temperature: rain (or strong melt) ADDS WATER WITHOUT ADDING HEIGHT, so a day
# whose SWE jumps while depth stays flat or drops is a rain/melt pulse, not a
# snowfall (snow raises both). The crust then fades as new snow accumulates on top.
#
# LIMITATION (why it's pillow-only): needs BOTH swe and depth -- the 36 SNOTEL +
# Mammoth. ACIS/ECCC/BC-SWS/Open-Meteo lack one channel, so buried_crust_index
# returns None there (unknown, not zero) and those mountains rely on the trailing
# refreeze signal alone. No temperature means this is inferential -- deliberately
# conservative thresholds, and it only touches the skiability headline + quality +
# commentary, never the self-relative overall score.
CRUST_MEMORY = {
    "lookback_days": 40,       # how far back a buried crust still matters
    "rain_swe_in": 0.4,        # SWE rise (in) on a flat/dropping-depth day = a pulse
    "rain_swe_full_in": 1.2,   # SWE rise for a full-severity crust
    "depth_flat_in": 1.0,      # depth rose <= this on the pulse day -> not snowfall
    "bury_zero_in": 2.0,       # new snow since the pulse below this -> crust exposed
    "bury_full_in": 24.0,      # new snow since above this -> crust deeply buried
}

# Snowfall-weighted recent air temperature (F) -> new-snow water fraction (Tier 2).
# The classic snow-to-liquid relationship: very cold snow is fluffy (~5% water,
# ~20:1), snow near freezing is dense/wet (~14-20%, ~5-7:1). Clamped at both ends.
DENSITY_FROM_TEMP = [(10, 0.05), (20, 0.075), (28, 0.11), (32, 0.14), (36, 0.20)]

# New-snow water fraction -> 0-100 density QUALITY (higher = lighter = better).
# ~5-7% is blower/champagne (top marks); ~10% is a normal storm; ~13%+ is heavy;
# ~20%+ is Sierra cement / rain-tinged. Absolute cutoffs by what the number means.
DENSITY_SCORE_CURVE = [
    (0.04, 100), (0.07, 92), (0.10, 76), (0.13, 54),
    (0.17, 32), (0.22, 14), (0.30, 5),
]

# New-snow water fraction -> multiplier on RECENT powder inches (score.
# effective_powder_in). Deliberately gentle -- density shades how much a fresh
# inch is worth, it doesn't erase it: light snow gets full credit (1.0), classic
# cement (~20%) counts for ~0.7 of its depth. Floored so it never zeroes out real
# accumulation. A heavy foot still beats a dry inch.
DENSITY_POWDER_FACTOR = [
    (0.07, 1.00), (0.10, 0.96), (0.13, 0.88), (0.17, 0.78), (0.22, 0.68),
    (0.30, 0.55),   # rain-saturated slush; clamps at the floor below
]
DENSITY_POWDER_FLOOR = 0.62

# Absolute skiability value (0-100) -> letter. Unlike GRADE_THRESHOLDS (uniform
# percentiles) this scores a real inches-based quantity, so the cutoffs are
# placed by what the number MEANS, then sanity-checked against the live roster's
# distribution (see docs/tuning.md): A+ is a deep base + a real storm in good
# weather; a deep-base-no-fresh day sits ~B-; a thin or raining day falls to D/F.
SKIABILITY_GRADE_THRESHOLDS = [
    (86, "A+"),
    (76, "A"),
    (67, "A-"),
    (58, "B+"),
    (49, "B"),
    (41, "B-"),
    (33, "C+"),
    (25, "C"),
    (18, "C-"),
    (11, "D"),
    (0,  "F"),
]

# ---------------------------------------------------------------------------
# Cover gate -- skiability is multiplicative, not additive
# ---------------------------------------------------------------------------
# The overall score is scaled by a factor derived from ABSOLUTE snow cover:
#   factor = floor + (1 - floor) * depth_score(effective_depth) / 100
# A percentile blend alone lets an 8" base at its 90th percentile outrank a 50"
# base at its 50th; the gate encodes "thin cover caps the day no matter how good
# a year the hill is having". Stations without a depth sensor fall back to a
# settled-depth proxy (see pipeline.settled_cover_depth) so they can't dodge the
# gate by simply not reporting depth.
#
# The ratios are STOCK conversions -- they turn a current reading into "how much
# settled snow is on the ground right now". They were once applied to the
# season-to-date TOTAL, which is a flow that never decays: in July that told the
# gate Alta had 100" of base off 33" of accumulated water, and the gate opened
# all the way for a bare mountain. Apply them to current readings only.
COVER_GATE = {
    "floor": 0.35,                  # multiplier when there is zero cover
    "snowfall_settle_ratio": 0.30,  # trailing 30d snowfall -> settled-depth proxy
    "swe_to_depth_ratio": 3.0,      # current SWE -> settled depth (~33% density)
}

# ---------------------------------------------------------------------------
# In-season gate -- is there enough snow to ski AT ALL?
# ---------------------------------------------------------------------------
# The cover gate caps a score; this decides whether a score means anything.
#
# The within-region number is a PERCENTILE against regional peers, and a
# percentile cannot tell that the whole region is bottomed out. In July, Palisades
# ranked 100th in Tahoe -- an A+ -- purely because every mountain around it was
# equally bare. The rank was arithmetically correct and completely misleading.
#
# So skiability gets an ABSOLUTE test, from fields already in raw_observations:
#   cover depth : snow_depth_inches, else swe_inches * swe_to_depth_ratio, else
#                 trailing-30d new_snow_24hr * snowfall_settle_ratio
#   fresh snow  : new_snow_24hr summed over FRESH_WINDOW_DAYS
#
# A mountain is in season when EITHER holds. The OR matters: `min_depth_in` alone
# at 12" declares Killington, Stowe, Sugarbush, Sunday River, Sugarloaf and
# Bachelor closed in mid-January, because those sit on ACIS COOP *valley* stations
# that systematically under-report mountain base. 6" plus a fresh-snow escape
# hatch keeps them honest without laundering a dead week into a good one.
#
# Deliberately NOT a calendar check. Validated against the real DB, this rule
# separates the hemispheres from snow data alone -- Jan 15: 49 northern / 0
# southern in season; Jul 10: 0 northern / 9 southern. A warm, snowless January
# week grades exactly like July, which is the point.
#
# Known limitation: the gate is only as good as the station. In midwinter it still
# flags fernie, killington, sugarbush, sun_peaks and sunday_river as off-season --
# all ECCC/ACIS valley stations reading 2-5" of base. That is a station-siting
# problem to fix upstream, not a reason to loosen the threshold until it means
# nothing.
#
# `carry_forward_days` handles seasonal stations. Stratton's ACIS station has
# 24,280 depth readings since 1941 and no SWE sensor; every summer it reports
# 0.0" and then goes quiet. On Jul 10 its last reading was 0.0" on May 31 -- a
# KNOWN ZERO, 40 days stale. A hard recency cliff turned that into "unknown", the
# cover gate never engaged, and a bare mountain sat 2nd on the global leaderboard.
#
# Snow cover cannot appear without snowfall. So a stale reading that was BELOW the
# threshold, with no snowfall reported since, is carried forward as real: evidence
# of no snow, not absence of evidence. Only sub-threshold readings carry -- a
# station that went quiet holding 40" of base might have melted out, and we don't
# know. The age bound stops a station that died mid-autumn at 0" from being
# declared bare all winter (Nov 1 -> Jan 15 is 75 days, past the limit).
IN_SEASON_GATE = {
    "min_depth_in": 6.0,       # settled cover that counts as skiable
    "min_fresh_7d_in": 3.0,    # ...or this much new snow in the last week
    "recency_days": 7,         # how stale a depth/SWE reading may be and still count
    "carry_forward_days": 60,  # ...unless it was a sub-threshold reading (see above)
}

# An off-season mountain keeps a sortable number (so the leaderboard still orders
# it) but must never present as a good day. The cover gate alone isn't enough: its
# floor of 0.35 still admits an overall of ~43, which reads as a "B". Clamping at
# 12 puts the ceiling in D territory on OVERALL_GRADE_THRESHOLDS while preserving
# the ordering of everything already below it (min(), not assignment).
OFF_SEASON = {
    "overall_cap": 12.0,
}

# ---------------------------------------------------------------------------
# Staleness -- a live grade needs a station that is still reporting
# ---------------------------------------------------------------------------
# The cover gate and the in-season gate already handle a station reporting a
# stale-but-known BARE reading (see IN_SEASON_GATE.carry_forward_days). This is
# the OTHER tail: a station that has gone entirely silent. `settled_cover_depth`
# then returns None, the cover gate disengages (factor 1.0) and `is_in_season`
# returns None (unknown) -- so nothing caps an overall that is still riding the
# season-to-date percentile with no current evidence behind it.
#
# In the current DB no mountain actually trips this (verified: 0 stale stations
# grade C+ or better on 2026-01-15 or 2026-07-14), because a frozen current-season
# total falls behind the fully-reported historical years as the winter goes on.
# `apply_stale_cap` is the guard that keeps it that way if an upstream dies mid-
# storm, and `data_age_days` / `stale` on the card surface a quiet station instead
# of letting it be silently trusted.
DATA_STALE_DAYS = 21             # no obs of ANY kind in this many days -> stale
STALE_UNKNOWN_COVER_CAP = 20.0   # cap the overall (~C) when stale AND cover unknown

# Incremental ingest: when a station already has stored history, re-fetch only
# from (its latest stored date - this many days) forward, not the whole period
# of record. The overlap re-pulls the recent tail so upstream revisions (SNOTEL
# and ERA5 both revise the last week or two) land via upsert; deep history is
# immutable and never re-fetched. An empty station still does a full-history
# pull, so first run and any cache loss self-heal.
INGEST_OVERLAP_DAYS = 14

# ---------------------------------------------------------------------------
# Per-mountain base offset -- the valley-station under-read correction (opt-in)
# ---------------------------------------------------------------------------
# A MOUNTAINS entry may set `base_offset_in`: inches to ADD to a station's
# measured settled cover so the ABSOLUTE gates (cover_factor, is_in_season) see
# the mountain's base rather than a valley COOP station's. This is the hook the
# audit's #2 fix hangs on; it does NOT touch the RELATIVE base percentile (a
# constant offset cancels out of a same-station percentile), only the two absolute
# skiability gates that valley siting distorts (Killington/Stowe/Sugarbush/Sunday
# River/Fernie/Sun Peaks read 2-5" valley base in midwinter and get called closed).
#
# It applies ONLY to a POSITIVE reading (see settled_cover_depth): a station that
# reads 0" in July is genuinely bare and must stay bare, so the offset never
# manufactures summer cover. Default 0.0 -> inert for every mountain that doesn't
# set it. Populating real values needs station-vs-summit elevation data we don't
# store yet; left opt-in rather than guessed (this codebase does not fake signal).
DEFAULT_BASE_OFFSET_IN = 0.0

# ---------------------------------------------------------------------------
# Forecast horizon blend -- Part 2: sharper on INCOMING conditions
# ---------------------------------------------------------------------------
# The forecast sub-score used to rank a single window (whichever of 24h/72h had
# the bigger storm) against storm history. Forecast skill degrades fast past
# ~3 days, so the incoming-snow percentile that feeds score.forecast_score is now
# a WEIGHTED BLEND across three horizons, near-term weighted heaviest (see
# pipeline.weighted_incoming_percentile). Weights needn't sum to 1 -- they're
# renormalized over whichever horizons actually rank against something.
#
# This stays a single number feeding the existing overall + region-rank design
# (no separate forecast grade on the card) -- sharpening what forecast already
# contributes, not restructuring how it's shown.
FORECAST_HORIZONS_HOURS = (24, 48, 72)
FORECAST_HORIZON_WEIGHTS = {24: 0.50, 48: 0.30, 72: 0.20}

# ---------------------------------------------------------------------------
# Medium-range tier -- 4-10 days out, a RANGE not a point estimate
# ---------------------------------------------------------------------------
# The near-term blend above (24/48/72h) is skillful and reported as a single
# number. Medium-range skill is real but coarse, so it's reported as a
# low/mid/high band (see ski.sources.outlook.medium_range_band) and folded into
# the forecast sub-score at a small, confidence-tapered weight rather than
# treated as a fourth equal horizon.
#
#   horizon_hours       -- target outer edge, 10 days. A source that can't
#                          reach it (NWS gridpoints run ~7 days) just reports a
#                          narrower, more-confident band instead of padding out.
#   min_hours           -- below this much forward coverage there's no medium-
#                          range tier at all (None), not a fabricated one.
#   band_width_at_min/full -- how wide the +/- band is at the two ends of the
#                          min_hours..horizon_hours span, interpolated by how
#                          far the actual window reaches. Widens with distance:
#                          a barely-4-day window is this tier's best case, a
#                          full 10-day window its worst.
#   weight              -- this tier's BASE share of the blended forecast
#                          percentile (near-term implicitly weights 1.0), before
#                          the same distance fraction tapers it down (see
#                          pipeline.combine_forecast_percentile). A full 10-day
#                          reach carries only 40% of even this small weight.
MEDIUM_RANGE = {
    "horizon_hours": 240,
    "min_hours": 96,
    "band_width_at_min": 0.20,
    "band_width_at_full": 0.65,
    "weight": 0.12,
}

# ---------------------------------------------------------------------------
# Global / Regional Comparable Score -- cross-mountain, not self-relative
# ---------------------------------------------------------------------------
# `overall` (score.overall_score) answers "is this a good year/day FOR THIS
# MOUNTAIN": two of its four sub-scores (season, base) are percentiles against
# the mountain's OWN history, so a resort having its best season ever can
# outrank a resort having an ordinary one -- exactly backwards for "where
# should I go ski right now". ski.comparable answers that question instead,
# from FOUR ABSOLUTE (inches) inputs percentile-ranked across a POPULATION OF
# MOUNTAINS -- the whole roster for `global_score`, one region for
# `regional_score` -- recomputed fresh on every run, never against history:
#   base     -- current settled cover depth (pipeline.settled_cover_depth):
#               already the one place every data source's depth/SWE/snowfall
#               reading gets unified into a single "inches on the ground" figure.
#   fresh    -- trailing COMPARABLE_FRESH_WINDOW_DAYS of new snow -- a SHORTER,
#               more "right now" window than the 7-day figure the `conditions`
#               sub-score uses, deliberately: this score asks "did it just
#               snow", not "how was the week".
#   season   -- season-to-date total, unit-converted to snow-equivalent inches
#               when the metric is swe_gain (see pipeline.season_snow_equivalent_in)
#               so a water-inches station and a snow-inches station pool together.
#   forecast -- next-72h phase-adjusted expected snowfall (absolute inches, not
#               the self-relative forecast_sub percentile) -- "is more coming".
#
# `quality` (Phase 4) is the SnowQuality value -- density + wind + crust + thaw +
# warmth folded into one 0-100 number -- and it's what finally addresses the
# "dense maritime inch vs. dry continental inch" limitation this docstring used to
# just flag: cold, dry, calm snow now out-ranks a wind-hammered, crusty pile of the
# same depth. (The valley-siting / DEFAULT_BASE_OFFSET_IN limitation is still open.)
#
# Forecast carries more weight than a "what's on the ground" purist would give
# it: a big incoming storm is a primary reason to pick a mountain right now, so
# the RANK reflects it too (the skiability headline gets its own forecast bump
# via POWDER_SCORE_CURVE). base still leads -- coverage is the precondition.
#
# `quality` starts at a MODERATE share (~13% effective) on purpose: half of it
# rides on coarse Open-Meteo wind, so we ramp it up only after validating the
# reorderings it produces on the live roster (user decision, 2026-07-17). Weights
# needn't sum to 1 -- ski.comparable._blend renormalizes over whatever's present.
GLOBAL_SCORE_WEIGHTS = {"base": 0.30, "fresh": 0.26, "season": 0.14,
                        "forecast": 0.22, "quality": 0.16}

# Trailing window (days) for the comparable score's "fresh" input -- shorter
# than FRESH_WINDOW_DAYS (7d) on purpose (see GLOBAL_SCORE_WEIGHTS docstring).
COMPARABLE_FRESH_WINDOW_DAYS = 3

# Temperature-based precip-phase correction. A provider's forecast snowfall is
# already phase-classified at ITS OWN grid point, which can sit below a resort's
# actual elevation -- so this is a second, conservative check using the forecast
# temperature itself: forecast precip at 38F is rain, not powder, full stop,
# regardless of what the provider's own snow/rain split said.
# Full credit as snow at/below `snow_full_f`; zero credit (all rain) at/above
# `rain_full_f`; linear between. Missing temp -> full credit (unknown != warm).
PRECIP_PHASE = {
    "snow_full_f": 32,
    "rain_full_f": 38,
}

# On-disk SQLite location. Defaults to this project directory so the CLI and API
# work regardless of the current working directory (e.g. uvicorn --app-dir).
#
# SKI_DB_PATH overrides it. Deployments MUST set this to a path on a persistent
# volume: the container filesystem is ephemeral, so a redeploy would silently
# discard 1.2M observations and every cached score. See fly.toml / DEPLOY.md.
import os as _os
DB_PATH = _os.environ.get("SKI_DB_PATH") or _os.path.join(
    _os.path.dirname(_os.path.abspath(__file__)), "data", "ski.db")

# ---------------------------------------------------------------------------
# Commentary engine -- which generator writes the one-line grade explanation
# ---------------------------------------------------------------------------
# "rules" (default): ski/commentary_rules.py -- pure deterministic templates over
#   the scorecard's own numbers. No API call, no ANTHROPIC_API_KEY, no external
#   dependency. This is what runs in production today.
# "ai": ski/commentary.py -- one claude-opus-4-8 call per (mountain, day), cached
#   in SQLite. Fully built and ready, but silently yields null without an
#   ANTHROPIC_API_KEY configured in the deploy environment.
#
# Both paths go through commentary.get_or_generate and write the SAME card field
# (card["commentary"]), so flipping this is the only change needed to switch. The
# COMMENTARY_MODE env var overrides this default (e.g. set it in the GitHub Action
# once the key exists) without a code edit.
COMMENTARY_MODE = _os.environ.get("COMMENTARY_MODE", "rules")

# ---------------------------------------------------------------------------
# Grade stability -- hysteresis so the OVERALL letter doesn't flap on noise
# ---------------------------------------------------------------------------
# `letter_grade` is a stateless threshold lookup with no memory of yesterday's
# grade, so a mountain sitting on a boundary (39.4 -> B, 38.6 -> B-) can cross it
# back and forth day to day on measurement noise alone. ski/stability.py adds a
# hysteresis band: once a grade is set, the raw value must clear the boundary by
# this many points -- in the direction of the move -- before the letter actually
# flips to an ADJACENT grade. A jump big enough to skip a grade entirely (a real
# storm) is real signal and is never held back.
#
# Scope: only the OVERALL letter, only for live (non-retro) scoring -- see
# ski/stability.py's docstring for what's deliberately out of scope and why.
GRADE_HYSTERESIS_MARGIN = 2.5          # score points past the boundary to flip
GRADE_HYSTERESIS_LOOKBACK_DAYS = 5     # how stale "yesterday's" grade may be and
                                        # still anchor today's (else raw stands)
