CASTD: Coastal Area and Soil Trend Detection (NASA Space Apps Challenge, Earth science trends)

Run: install Python (tick "Add to PATH"), double-click run.bat. Opens http://127.0.0.1:9021
This release uses port 9021 (every release uses a new port).

Two separate sections. The start screen asks which one you want:
  1. Coastal section: "Do you want to check a coastal area and assess its risk?"
     Main sea measures you can choose: sea surface temperature, wind, sea level, wave height, wave period.
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
