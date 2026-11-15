CASTD: Coastal Area and Soil Trend Detection (NASA Space Apps Challenge, Earth science trends)

Run: install Python (tick "Add to PATH"), double-click run.bat. Opens http://127.0.0.1:9027
This release uses port 9027 (every release uses a new port).

Two separate sections. The start screen asks which one you want:
  1. Coastal section: "Do you want to check a coastal area and assess its risk?"
     Main sea measures you can choose: sea surface temperature (NOAA satellite), wind, sea level, wave height, wave period.
     Everything else (air temperature, carbon dioxide, rain, clouds, El Nino and more) is a possible cause, with its own trend direction and trend line.
     You get alerts, a long-term outlook, a sea calendar with a swimming rating, a verdict and precaution policy.
  2. Soil section: "Do you want to find which areas of soil are slowly drying out?"
     Measures: surface, root-zone and whole-profile soil wetness (NASA POWER, no login or token needed).
     You get a drying ranking of places, alerts, outlook, possible causes, a farm calendar, a verdict and precaution policy.
  "Switch section" (top right) returns to the start screen. Each section keeps its own variables, alerts, causes and calendar.

How to use
  1. Pick a section. Click the map (or search a place) to add places.
  2. Choose measures, years and months. Run.
  3. Click a row: chart, tests, outlook (2030, 2050, 2100 with a range), "Find possible causes", "Recommendation and verdict".

Data
  NASA POWER (MERRA-2): soil wetness, sea surface temperature (model skin temperature), wind, air, rain, clouds, sunlight. 1981 to last year.
  Optional sea extras (not NASA POWER, no login): sea level (PSMSL tide gauge), wave height and period (Open-Meteo Marine).
  NOT TESTED LIVE: these extras were written from documentation and tested only on simulated data. Run check_sources.bat once and report any FAIL line.
  NASA POWER sea surface temperature is a model value, so its trend can look steeper than satellite records.

Limits
  Data is regional (about 25 to 50 km), not one beach or one field.
  Alerts are rough guides, not official warnings. Follow lifeguards, flags and local authorities.
  The outlook is a straight line from the measured trend, not a forecast.
  Causes are association (evidence), not proof.

Extra data from Meteomatics (optional, off unless you set two secret settings)
  What it adds: only measures the app does not already have, so nothing is fetched twice:
  soil moisture deficit (how much water the soil is short of, in mm) and swell height. Soil wetness stays NASA POWER; wave height and period stay Open-Meteo. They appear in the measure list under "Extra ... (Meteomatics)".
  Your Meteomatics user name and password are NEVER saved in any file. The app reads them only from two environment variables:
      METEOMATICS_USER     METEOMATICS_PASS
  If they are not set, these rows do not appear and everything else works as before.
  On your own computer: open run.bat in Notepad and remove "rem" from the two "set" lines, then type your user name and password there.
     Do not share or upload run.bat after you type the password into it.
  On Render: your service > Environment > Add Environment Variable, one for each name above. Then Save (Render restarts the service).
  Each place and each Meteomatics measure uses about 11 requests (three years per request) for 1990 to now. Your plan has a request limit, so pick few measures.
  NOT TESTED LIVE: the web address form, the answer layout, and how far back your account can read were written from documentation.
  They were tested only on simulated answers. Run check_sources.bat after setting the two variables. A wrong login, no rights to a
  parameter, or a request limit shows as "No data" with the reason on that row; nothing else breaks.

Sources used for the coastal section (one source for each measure, chosen on meaning, trust and reach)
  Sea surface temperature   NOAA OISST satellite data (0.25 degree, daily, from 1982). Real observed sea temperature, any sea or ocean point.
                            The NASA POWER "skin temperature" is only a model value and can be land temperature near a shore, so it is not offered
                            as the measure. If NOAA does not answer, the row uses the NASA value and says so on that row.
  Wind speed at 10 m        NASA POWER (MERRA-2 model). Same meaning everywhere on Earth, 1981 to now. Nothing better is free for every point.
  Sea level                 PSMSL tide gauge (nearest gauge, within 250 km). Real measured sea level but only near a gauge. Far from one, the row says "No data".
  Wave height, wave period  Open-Meteo Marine (ECMWF and Meteo-France wave models). The only free wave source for any sea point. NASA POWER has no waves.
  NOT TESTED LIVE: NOAA, PSMSL and Open-Meteo were written from documentation and tested on simulated answers. Run check_sources.bat.

Climate model projection (new)
  Pick a future year (2026 to 2050) in step 2. At the END of the results a card shows, for each place, the change by that year from up to 7
  climate models (Open-Meteo Climate API: CMCC_CM2_VHR4, FGOALS_f3_H, HiRAM_SIT_HR, MRI_AGCM3_2_S, EC_Earth3P_HR, MPI_ESM1_2_XR, NICAM16_8S).
  Variables: wind, air temperature, rain, and surface soil water. Each model's change is measured against its own 1991 to 2020 mean, and the
  change is carried forward on top of your own latest data for wind and surface soil wetness.
  Limits: soil water exists in only 2 of the 7 models (the card says so). The models have no sea temperature, sea level or waves, so those keep the
  straight-line outlook. These are projections, not forecasts. The models end in 2050. NOT TESTED LIVE: tested on simulated answers only.

3D globe
  The map is now a 3D globe (MapLibre GL). Drag to turn it, scroll to zoom, click to add a place. The globe button (top right) flips it to a flat map.
  Map pictures come from OpenStreetMap, so it needs the internet.
