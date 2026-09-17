#!/usr/bin/env python3
"""Build ArcticSoak's versioned data publication and crawlable pages."""
import argparse, csv, html, json, math, os, re, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MONTHS=['January','February','March','April','May','June','July','August','September','October','November','December']
DAYS=[31,28,31,30,31,30,31,31,30,31,30,31]
TARGET_F=50.0; GALLONS=100; MODEL_VERSION='2.0.0'; LICENSE='CC BY 4.0'
CLIMATE_VINTAGE='NOAA 1991–2020 U.S. Climate Normals'
SCENARIOS={
 'efficient':{'label':'Efficient insulated','ua':10.0,'base_kwh_day':1.0,'cop':2.6,'allowance_kwh_day':.15},
 'reference':{'label':'Reference consumer setup','ua':18.0,'base_kwh_day':1.6,'cop':2.2,'allowance_kwh_day':.55},
 'high_load':{'label':'Lightly insulated / exposed','ua':32.0,'base_kwh_day':2.4,'cop':1.8,'allowance_kwh_day':1.0},
}
STATE_NAMES={'AK':'Alaska','AL':'Alabama','AR':'Arkansas','AZ':'Arizona','CA':'California','CO':'Colorado','CT':'Connecticut','DC':'District of Columbia','FL':'Florida','GA':'Georgia','HI':'Hawaii','IA':'Iowa','ID':'Idaho','IL':'Illinois','IN':'Indiana','KS':'Kansas','KY':'Kentucky','LA':'Louisiana','MA':'Massachusetts','MD':'Maryland','ME':'Maine','MI':'Michigan','MN':'Minnesota','MO':'Missouri','MS':'Mississippi','NC':'North Carolina','ND':'North Dakota','NE':'Nebraska','NH':'New Hampshire','NJ':'New Jersey','NM':'New Mexico','NV':'Nevada','NY':'New York','OH':'Ohio','OK':'Oklahoma','OR':'Oregon','PA':'Pennsylvania','RI':'Rhode Island','SC':'South Carolina','SD':'South Dakota','TN':'Tennessee','TX':'Texas','UT':'Utah','VA':'Virginia','VT':'Vermont','WA':'Washington','WI':'Wisconsin','WV':'West Virginia'}

def esc(v): return html.escape(str(v),quote=True)
def write(path,content):
 p=ROOT/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content.strip()+'\n',encoding='utf-8')
def get_json(url,timeout=45,attempts=3):
 error=None
 for attempt in range(attempts):
  try:
   req=urllib.request.Request(url,headers={'User-Agent':'ArcticSoakData/2.0 (+https://arcticsoak.com/methodology/)','Accept':'application/json'})
   with urllib.request.urlopen(req,timeout=timeout) as response:return json.loads(response.read().decode('utf-8'))
  except Exception as exc:
   error=exc
   if attempt+1<attempts:time.sleep(1.5*(attempt+1))
 raise error
def load_cities():
 with open(ROOT/'data/cities.csv',newline='',encoding='utf-8') as f:return list(csv.DictReader(f))
def load_seed_rates():
 raw=json.loads((ROOT/'data/electricity_seed.json').read_text())
 return {s:{'rate':float(r),'period':'bundled planning value','source':'Bundled planning rate'} for s,r in raw.items()}
def load_existing():
 try:return json.loads((ROOT/'data/index.json').read_text())
 except:return {'cities':[]}
def fetch_eia_rates(api_key):
 if not api_key:return None
 params=[('api_key',api_key),('frequency','monthly'),('data[]','price'),('facets[sectorid][]','RES'),('sort[0][column]','period'),('sort[0][direction]','desc'),('offset','0'),('length','5000')]
 rows=get_json('https://api.eia.gov/v2/electricity/retail-sales/data/?'+urllib.parse.urlencode(params)).get('response',{}).get('data',[]);latest={}
 for row in rows:
  state,price=row.get('stateid'),row.get('price')
  if not state or len(state)!=2 or state in latest or price in (None,''):continue
  try:latest[state]={'rate':round(float(price)/100,4),'period':str(row.get('period') or 'latest monthly release'),'source':'U.S. EIA residential average retail price'}
  except:pass
 return latest or None
def synthetic_normals(mean,amplitude):
 out=[]
 for m in range(12):
  avg=mean+amplitude*math.cos(2*math.pi*(m-6.6)/12);diurnal=max(12,min(24,15+amplitude*.16))
  out.append({'avg':round(avg,1),'min':round(avg-diurnal/2,1),'max':round(avg+diurnal/2,1)})
 return out
def month_from_record(record,fallback):
 for token in reversed([int(x) for x in re.findall(r'\d+',str(record.get('DATE','')))]):
  if 1<=token<=12:return token
 return fallback
def fetch_noaa_normals(lat,lon):
 for pad in (.22,.5,1.0):
  q={'dataset':'normals-monthly-1991-2020','dataTypes':'MLY-TAVG-NORMAL,MLY-TMAX-NORMAL,MLY-TMIN-NORMAL','bbox':f'{lat+pad},{lon-pad},{lat-pad},{lon+pad}','format':'json','includeStationName':'true','includeStationLocation':'true','units':'standard'}
  try:rows=get_json('https://www.ncei.noaa.gov/access/services/data/v1?'+urllib.parse.urlencode(q))
  except:continue
  stations={}
  for row in rows if isinstance(rows,list) else []:stations.setdefault(row.get('STATION','unknown'),[]).append(row)
  candidates=[]
  for sid,recs in stations.items():
   monthly={}
   for idx,r in enumerate(recs):
    month=month_from_record(r,idx+1 if len(recs)==12 else 0)
    if not month:continue
    def num(*keys):
     for key in keys:
      if r.get(key) not in (None,''):
       try:return float(r[key])
       except:pass
    avg=num('MLY-TAVG-NORMAL','mly-tavg-normal');hi=num('MLY-TMAX-NORMAL','mly-tmax-normal');lo=num('MLY-TMIN-NORMAL','mly-tmin-normal')
    if avg is not None:monthly[month]={'avg':avg,'min':lo,'max':hi}
   if len(monthly)<10:continue
   first=recs[0]
   try:dist=(float(first.get('LATITUDE',lat))-lat)**2+(float(first.get('LONGITUDE',lon))-lon)**2
   except:dist=999
   candidates.append((len(monthly),-dist,sid,monthly,first.get('NAME') or first.get('STATION_NAME') or sid))
  if candidates:
   _,_,sid,monthly,name=sorted(candidates,reverse=True)[0];fallback=sum(x['avg'] for x in monthly.values())/len(monthly);out=[]
   for m in range(1,13):
    item=monthly.get(m,{'avg':fallback,'min':None,'max':None});avg=item['avg'];lo=item['min'] if item['min'] is not None else avg-9;hi=item['max'] if item['max'] is not None else avg+9
    out.append({'avg':round(avg,1),'min':round(lo,1),'max':round(hi,1)})
   return out,sid,name
 return None,None,None
def fetch_recent(lat,lon):
 end=datetime.now(timezone.utc).date()-timedelta(days=2);start=end-timedelta(days=30)
 for pad in (.2,.5):
  q={'dataset':'daily-summaries','dataTypes':'TAVG,TMAX,TMIN','bbox':f'{lat+pad},{lon-pad},{lat-pad},{lon+pad}','startDate':str(start),'endDate':str(end),'format':'json','includeStationName':'true','includeStationLocation':'true','units':'standard'}
  try:rows=get_json('https://www.ncei.noaa.gov/access/services/data/v1?'+urllib.parse.urlencode(q),attempts=2)
  except:continue
  groups={}
  for row in rows if isinstance(rows,list) else []:groups.setdefault(row.get('STATION','unknown'),[]).append(row)
  best=None
  for sid,recs in groups.items():
   vals=[]
   for r in recs:
    try:
     if r.get('TAVG') not in (None,''):vals.append(float(r['TAVG']))
     elif r.get('TMAX') not in (None,'') and r.get('TMIN') not in (None,''):vals.append((float(r['TMAX'])+float(r['TMIN']))/2)
    except:pass
   if len(vals)>=15 and (best is None or len(vals)>best[0]):best=(len(vals),sum(vals)/len(vals),sid,recs[0].get('NAME') or sid)
  if best:return round(best[1],1),best[2],best[3],str(start),str(end)
 return None,None,None,None,None
def positive_degree_average(low,high,target):
 mean=(low+high)/2;amp=max((high-low)/2,0);samples=[mean+amp*math.sin(2*math.pi*h/24-math.pi/2) for h in range(24)]
 return sum(max(v-target,0) for v in samples)/24
def model_scenario(normals,rate,scenario,target=TARGET_F):
 monthly=[];annual_kwh=annual_cost=degree_hours=0
 for i,climate in enumerate(normals):
  delta=positive_degree_average(climate['min'],climate['max'],target);cop=max(1.2,scenario['cop']-max(climate['max']-75,0)*.018)
  cooling=scenario['ua']*delta*24/3412/cop;kwh=(scenario['base_kwh_day']+scenario['allowance_kwh_day']+cooling)*DAYS[i];cost=kwh*rate
  annual_kwh+=kwh;annual_cost+=cost;degree_hours+=delta*24*DAYS[i]
  monthly.append({'month':MONTHS[i],'temp_f':round(climate['avg'],1),'normal_low_f':round(climate['min'],1),'normal_high_f':round(climate['max'],1),'positive_delta_f':round(delta,1),'kwh':round(kwh,1),'cost':round(cost,2)})
 peak=max(monthly,key=lambda x:x['cost'])
 return {'annual_kwh':round(annual_kwh,1),'annual_cost':round(annual_cost,2),'monthly_average_cost':round(annual_cost/12,2),'peak_month':peak['month'],'peak_month_cost':peak['cost'],'cooling_degree_hours':round(degree_hours),'months':monthly}
def compute_city(row,rate_record,existing,offline=False,skip_recent=False):
 lat,lon=float(row['lat']),float(row['lon']);normals=station=station_name=None;source='Seed approximation — provisional'
 if not offline:normals,station,station_name=fetch_noaa_normals(lat,lon)
 if normals:source=CLIMATE_VINTAGE
 else:
  old_source=existing.get('climate_source') or existing.get('normal_source','');old=existing.get('climate_normals',[])
  if old_source.startswith('NOAA') and len(old)==12:normals,source=old,old_source;station=existing.get('noaa_station_id') or existing.get('noaa_station');station_name=existing.get('noaa_station_name')
  else:normals=synthetic_normals(float(row['annual_mean_f']),float(row['amplitude_f']))
 recent=recent_sid=recent_name=recent_start=recent_end=None
 if not offline and not skip_recent:recent,recent_sid,recent_name,recent_start,recent_end=fetch_recent(lat,lon)
 rate=rate_record['rate'];scenarios={k:model_scenario(normals,rate,v) for k,v in SCENARIOS.items()};ref=scenarios['reference']
 targets={str(t):model_scenario(normals,rate,SCENARIOS['reference'],t)['annual_cost'] for t in (55,50,45,39)}
 climate_score=max(0,100*(1-min(ref['cooling_degree_hours']/360000,1)));cost_score=max(0,100*(1-min(ref['annual_cost']/900,1)))
 return {'city':row['city'],'state':row['state'],'slug':row['slug'],'lat':lat,'lon':lon,'score':round(.75*climate_score+.25*cost_score,1),'rate':rate,'rate_period':rate_record['period'],'electricity_source':rate_record['source'],'rate_geography':'State residential average','climate_source':source,'climate_status':'verified' if source.startswith('NOAA') else 'provisional','climate_normals':normals,'noaa_station_id':station,'noaa_station_name':station_name,'recent_30d_avg_f':recent,'recent_station_id':recent_sid,'recent_station_name':recent_name,'recent_period_start':recent_start,'recent_period_end':recent_end,'scenarios':scenarios,'target_costs':targets,'annual_kwh':ref['annual_kwh'],'annual_cost':ref['annual_cost'],'peak_month':ref['peak_month'],'months':ref['months']}
def schema_script(schema):return f'<script type="application/ld+json">{json.dumps(schema,separators=(",",":"))}</script>' if schema else ''
def page_shell(title,desc,body,canonical,data,schema=None):
 status=f"{data['verified_climate_records']}/{len(data['cities'])} city climate records NOAA-verified" if data['verified_climate_records'] else 'Provisional climate inputs clearly labeled'
 footer='''<footer class="footer"><div class="wrap footer-grid"><div><a class="brand" href="/">ARCTIC<span>SOAK</span></a><p>Open, versioned planning data for cold-plunge energy and climate questions.</p></div><div><h3>Explore</h3><p><a href="/data/">Data</a> · <a href="/research/">Research</a> · <a href="/methodology/">Methodology</a> · <a href="/about/">About</a></p><p><a href="/editorial-standards/">Standards</a> · <a href="/corrections/">Corrections</a> · <a href="/recommended-retailer/">Where to buy</a></p><p class="tiny">Planning estimates, not product guarantees or medical advice. Dataset licensed CC BY 4.0.</p></div></div></footer>'''
 return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><meta name="description" content="{esc(desc)}"><link rel="canonical" href="https://arcticsoak.com{canonical}"><link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="/assets/styles.css"><meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(desc)}"><meta property="og:type" content="website"><meta property="og:url" content="https://arcticsoak.com{canonical}">{schema_script(schema)}</head><body><a class="skip" href="#main">Skip to content</a><div class="topbar"><div class="wrap"><span>Cold Plunge Climate &amp; Cost Index</span><span>{esc(status)}</span></div></div><nav class="nav" aria-label="Primary"><div class="wrap"><a class="brand" href="/">ARCTIC<span>SOAK</span></a><div class="navlinks"><a href="/cities/">Cities</a><a href="/states/">States</a><a href="/calculators/cost/">Tools</a><a href="/research/">Research</a><a href="/data/">Data</a><a href="/methodology/">Methodology</a></div></div></nav>{body}{footer}</body></html>'''

def scenario_rows(city):
 return ''.join(f'''<tr><th>{esc(SCENARIOS[k]['label'])}</th><td>{city['scenarios'][k]['annual_kwh']:,.0f} kWh</td><td>${city['scenarios'][k]['monthly_average_cost']:,.0f}/mo</td><td>${city['scenarios'][k]['annual_cost']:,.0f}/yr</td></tr>''' for k in ('efficient','reference','high_load'))

def generate_city(city,data,median):
 position='below' if city['annual_cost']<median else 'above';difference=abs(city['annual_cost']-median)/median*100 if median else 0
 warning='' if city['climate_status']=='verified' else '<div class="notice warning"><strong>Provisional climate input.</strong> This page currently uses a labeled seed approximation. The updater will replace it only after retrieving a complete NOAA station record.</div>'
 station=f"{esc(city['noaa_station_name'])} ({esc(city['noaa_station_id'])})" if city.get('noaa_station_id') else 'Not yet resolved — provisional seed profile'
 months=''.join(f'''<tr><th>{m['month']}</th><td>{m['normal_low_f']:.0f}–{m['normal_high_f']:.0f}°F</td><td>{m['kwh']:.0f} kWh</td><td>${m['cost']:.0f}</td></tr>''' for m in city['months'])
 targets=''.join(f'''<tr><th>{target}°F</th><td>${city['target_costs'][target]:,.0f}/year</td></tr>''' for target in ('55','50','45','39'))
 answer=f"ArcticSoak estimates a planning range of <strong>${city['scenarios']['efficient']['annual_cost']:,.0f}–${city['scenarios']['high_load']['annual_cost']:,.0f} per year</strong> for the standardized 100-gallon outdoor plunge in {esc(city['city'])}, with a <strong>${city['annual_cost']:,.0f}/year reference scenario</strong>."
 embed=esc(f'''<iframe src="https://arcticsoak.com/embed/city/?city={city['slug']}" title="Cold plunge cost in {city['city']}, {city['state']}" width="420" height="260" loading="lazy"></iframe><p><a href="https://arcticsoak.com/cities/{city['slug']}/">Cold plunge cost in {city['city']}, {city['state']} — ArcticSoak</a></p>''')
 schema={'@context':'https://schema.org','@type':'Dataset','name':f"Cold plunge operating-cost scenarios for {city['city']}, {city['state']}",'description':f"Versioned planning estimates for a 100-gallon outdoor cold plunge in {city['city']}.",'url':f"https://arcticsoak.com/cities/{city['slug']}/",'dateModified':data['generated_at'],'license':'https://creativecommons.org/licenses/by/4.0/','isBasedOn':'https://arcticsoak.com/methodology/','distribution':{'@type':'DataDownload','encodingFormat':'application/json','contentUrl':f"https://arcticsoak.com/data/cities/{city['slug']}.json"}}
 body=f'''<header class="page-hero"><div class="wrap"><div class="crumb"><a href="/">ArcticSoak</a> / <a href="/cities/">Cities</a> / {esc(city['city'])}</div><div class="eyebrow">City planning report · Model {MODEL_VERSION}</div><h1>Cold plunge cost in {esc(city['city'])}, {esc(city['state'])}</h1><p class="lede">{answer}</p></div></header><main id="main" class="wrap">{warning}<section class="city-dashboard"><div class="score-card"><div class="reading-label">Reference annual cost</div><div class="hero-number">${city['annual_cost']:,.0f}</div><p>${city['scenarios']['reference']['monthly_average_cost']:,.0f} average per month · {city['annual_kwh']:,.0f} kWh/year</p><div class="badge">ArcticSoak Score {round(city['score'])}/100</div></div><div class="metrics"><div class="metric"><small>Modeled planning range</small><strong>${city['scenarios']['efficient']['annual_cost']:,.0f}–${city['scenarios']['high_load']['annual_cost']:,.0f}</strong></div><div class="metric"><small>Peak month</small><strong>{esc(city['peak_month'])}</strong></div><div class="metric"><small>Electricity input</small><strong>{city['rate']*100:.1f}¢/kWh</strong></div><div class="metric"><small>Vs. indexed-city median</small><strong>{difference:.0f}% {position}</strong></div></div></section><section class="section"><div class="section-head"><div><div class="eyebrow">Scenario range</div><h2>One city, three plausible setups</h2></div><p>These are sensitivity scenarios—not a confidence interval. The reference setup is the ranking standard.</p></div><div class="table-scroll"><table class="data-table"><thead><tr><th>Setup</th><th>Annual energy</th><th>Monthly average</th><th>Annual cost</th></tr></thead><tbody>{scenario_rows(city)}</tbody></table></div></section><section class="split"><div><h2>What colder targets change</h2><p>Lower settings increase positive temperature degree-hours and chiller runtime. The same reference configuration is held constant.</p><div class="table-scroll"><table class="data-table"><thead><tr><th>Target</th><th>Reference cost</th></tr></thead><tbody>{targets}</tbody></table></div></div><div><h2>Data provenance</h2><dl class="provenance"><dt>Climate</dt><dd>{esc(city['climate_source'])}</dd><dt>Station</dt><dd>{station}</dd><dt>Electricity</dt><dd>{esc(city['electricity_source'])}, {esc(city['rate_period'])}; state-level input</dd><dt>Model</dt><dd>ArcticSoak {MODEL_VERSION}; generated {esc(data['generated_at'])}</dd></dl><p><a href="/data/cities/{city['slug']}.json">Download this city record (JSON)</a></p></div></section><section class="section"><div class="section-head"><div><div class="eyebrow">Reference scenario</div><h2>Monthly climate and cost profile</h2></div><p>Daily low/high normals become positive cooling degree-hours; provisional pages use approximate profiles until NOAA retrieval succeeds.</p></div><div class="table-scroll"><table class="data-table"><thead><tr><th>Month</th><th>Normal low–high</th><th>Energy</th><th>Cost</th></tr></thead><tbody>{months}</tbody></table></div></section><section class="split"><div class="citation-box"><h2>Cite this page</h2><p>ArcticSoak. “Cold Plunge Cost in {esc(city['city'])}, {esc(city['state'])}.” Model {MODEL_VERSION}, dataset {esc(data['dataset_version'])}. Accessed [date]. https://arcticsoak.com/cities/{city['slug']}/</p><button class="btn ghost" data-copy-citation>Copy citation</button></div><div class="citation-box"><h2>Embed this city card</h2><code id="embed-code">{embed}</code><p><button class="btn ghost" data-copy-target="embed-code">Copy embed code</button></p></div></section></main><script src="/assets/app.js" defer></script>'''
 write(f"cities/{city['slug']}/index.html",page_shell(f"Cold Plunge Cost in {city['city']}, {city['state']} | ArcticSoak",f"Modeled cold plunge electricity use and annual cost range in {city['city']}, with transparent climate, rate and scenario inputs.",body,f"/cities/{city['slug']}/",data,schema));write(f"data/cities/{city['slug']}.json",json.dumps(city,indent=2))

def generate_home(data):
 cities=data['cities'];lowest=sorted(cities,key=lambda c:c['annual_cost'])[:10];highest=max(cities,key=lambda c:c['annual_cost'])
 rows=''.join(f'''<tr><td class="rank-num">{i}</td><td><a class="city-link" href="/cities/{c['slug']}/">{esc(c['city'])}, {esc(c['state'])}</a></td><td>${c['annual_cost']:,.0f}</td><td>${c['scenarios']['efficient']['annual_cost']:,.0f}–${c['scenarios']['high_load']['annual_cost']:,.0f}</td><td>{esc(c['peak_month'])}</td></tr>''' for i,c in enumerate(lowest,1))
 provisional=len(cities)-data['verified_climate_records'];notice=f'''<div class="notice warning"><strong>Current data status:</strong> {provisional} city climate profiles remain provisional seed approximations. They are labeled on every affected page and are not represented as measured or verified performance. <a href="/data/">Review provenance →</a></div>''' if provisional else '<div class="notice good"><strong>Climate refresh complete.</strong> Every indexed city is tied to a NOAA 1991–2020 normals station.</div>'
 schema={'@context':'https://schema.org','@type':'Dataset','name':'ArcticSoak U.S. Cold Plunge Climate & Cost Index','description':'Versioned city-level scenarios for cold plunge electricity use and operating cost.','url':'https://arcticsoak.com/data/','dateModified':data['generated_at'],'version':MODEL_VERSION,'license':'https://creativecommons.org/licenses/by/4.0/','distribution':[{'@type':'DataDownload','encodingFormat':'text/csv','contentUrl':'https://arcticsoak.com/data/cold-plunge-index.csv'},{'@type':'DataDownload','encodingFormat':'application/json','contentUrl':'https://arcticsoak.com/data/cold-plunge-index.json'}]}
 options=''.join(f'''<option value="{esc(c['city']+', '+c['state'])}">''' for c in cities)
 body=f'''<header class="hero"><div class="wrap hero-grid"><div><div class="eyebrow">Open U.S. cold-plunge data</div><h1>Cold water,<br><em>measured honestly.</em></h1><p class="lede">Compare planning ranges for cold-plunge energy use, operating cost and cooling load—city by city, with the model, source status and limitations visible.</p><div class="hero-search"><label class="sr-only" for="city-search">Search a city</label><input id="city-search" list="city-options" placeholder="Search a city — e.g. Austin"><datalist id="city-options">{options}</datalist><button class="btn" id="city-search-button">Check city</button></div><p class="tiny">Reference: 100 gallons · outdoor · covered · 50°F target · Model {MODEL_VERSION}</p></div><div class="ice-card"><div class="reading-label">Dataset {esc(data['dataset_version'])}</div><div class="big-temp">50°</div><p>Three setup scenarios replace false single-number certainty.</p><div class="pulse-row"><div class="pulse"><small>Cities</small><strong>{len(cities)}</strong></div><div class="pulse"><small>NOAA verified</small><strong>{data['verified_climate_records']}</strong></div><div class="pulse"><small>Formats</small><strong>CSV + JSON</strong></div></div></div></div></header><main id="main"><div class="wrap">{notice}</div><section class="section"><div class="wrap"><div class="section-head"><div><div class="eyebrow">Crawlable national table</div><h2>Lowest reference costs in the current index</h2></div><p>Costs use the reference consumer scenario. Read the range and source status before citing a figure.</p></div><div class="rank-grid"><div class="panel table-scroll"><table class="rank-table"><thead><tr><th>#</th><th>City</th><th>Reference</th><th>Scenario range</th><th>Peak</th></tr></thead><tbody>{rows}</tbody></table></div><aside class="callout"><div class="reading-label">Highest reference cost</div><h3>{esc(highest['city'])}, {esc(highest['state'])}</h3><p>The local electricity input and climate profile both affect this comparison.</p><div class="metric-list"><div><span>Reference</span><strong>${highest['annual_cost']:,.0f}/yr</strong></div><div><span>Scenario range</span><strong>${highest['scenarios']['efficient']['annual_cost']:,.0f}–${highest['scenarios']['high_load']['annual_cost']:,.0f}</strong></div></div><p><a class="btn alt" href="/rankings/">View all cities</a></p></aside></div></div></section><section class="section surface"><div class="wrap"><div class="section-head"><div><div class="eyebrow">Use the data</div><h2>Answers before interactions.</h2></div><p>Every tool includes its formula, worked examples and a static reference table for readers and crawlers.</p></div><div class="tool-grid"><a class="tool" href="/calculators/cost/"><div class="num">TOOL 01</div><h3>Running cost</h3><p>Model monthly electricity by ambient temperature, target, setup and rate.</p></a><a class="tool" href="/calculators/chiller/"><div class="num">TOOL 02</div><h3>Chiller sizing</h3><p>Calculate water-only cooldown load, then add real-world headroom.</p></a><a class="tool" href="/calculators/ice/"><div class="num">TOOL 03</div><h3>Ice required</h3><p>Estimate ice pounds, bag count and cost with the full heat-balance formula.</p></a><a class="tool" href="/calculators/ice-vs-chiller/"><div class="num">TOOL 04</div><h3>Ice vs. chiller</h3><p>Estimate annual cost and simple payback using local ice and electricity prices.</p></a></div></div></section><section class="data-band"><div class="wrap"><div><strong>Reuse ArcticSoak data with attribution.</strong><div>Versioned CSV, JSON, city records, methodology and a copy-ready citation.</div></div><a class="btn" href="/data/">Open data portal →</a></div></section><section class="section"><div class="wrap"><div class="section-head"><div><div class="eyebrow">Research, not filler</div><h2>Facts designed to be checked.</h2></div><p>The annual index documents what the model can and cannot establish. The validation project will compare predictions with smart-plug measurements.</p></div><div class="tool-grid"><a class="tool" href="/research/2026-cold-plunge-cost-index/"><div class="num">2026 INDEX</div><h3>U.S. cost index</h3><p>National findings, tables, caveats and source status in one citable report.</p></a><a class="tool" href="/research/model-validation/"><div class="num">OPEN PROTOCOL</div><h3>Model validation</h3><p>The measurement plan and publication rules—without pretending results exist yet.</p></a><a class="tool" href="/methodology/"><div class="num">MODEL {MODEL_VERSION}</div><h3>Reproduce the math</h3><p>Constants, formulas, scenario definitions, version history and limitations.</p></a></div></div></section></main><script src="/assets/app.js" defer></script>'''
 write('index.html',page_shell('ArcticSoak — U.S. Cold Plunge Energy & Cost Data','Compare transparent cold-plunge energy and operating-cost scenarios by U.S. city. Versioned CSV/JSON data, formulas, sources and limitations included.',body,'/',data,schema))

def generate_rankings(data):
 rows=''.join(f'''<tr><td>{i}</td><td><a class="city-link" href="/cities/{c['slug']}/">{esc(c['city'])}, {esc(c['state'])}</a></td><td><span class="status {c['climate_status']}">{esc(c['climate_status'])}</span></td><td>${c['scenarios']['efficient']['annual_cost']:,.0f}</td><td><strong>${c['annual_cost']:,.0f}</strong></td><td>${c['scenarios']['high_load']['annual_cost']:,.0f}</td><td>{c['rate']*100:.1f}¢</td></tr>''' for i,c in enumerate(sorted(data['cities'],key=lambda x:x['annual_cost']),1))
 body=f'''<header class="page-hero"><div class="wrap"><div class="eyebrow">All {len(data['cities'])} indexed cities</div><h1>Cold plunge operating-cost rankings</h1><p class="lede">Ranked by the reference consumer scenario, with efficient and high-load sensitivities beside it.</p></div></header><main id="main" class="wrap"><div class="notice"><strong>Read the status column.</strong> Provisional rows use synthetic climate profiles and should not be described as NOAA-derived observations. Electricity is a state residential average.</div><section class="section"><div class="table-scroll"><table class="data-table"><thead><tr><th>Rank</th><th>City</th><th>Climate</th><th>Efficient</th><th>Reference</th><th>High-load</th><th>Rate</th></tr></thead><tbody>{rows}</tbody></table></div></section></main>'''
 write('rankings/index.html',page_shell('U.S. Cold Plunge Operating-Cost Rankings | ArcticSoak','Compare efficient, reference and high-load cold plunge energy-cost scenarios across U.S. cities with visible source status.',body,'/rankings/',data))
def generate_cities_index(data):
 cards=''.join(f'''<a class="city-card" href="/cities/{c['slug']}/"><span>{esc(c['state'])} · {esc(c['climate_status'])}</span><h2>{esc(c['city'])}</h2><p>${c['annual_cost']:,.0f}/yr reference</p><small>${c['scenarios']['efficient']['annual_cost']:,.0f}–${c['scenarios']['high_load']['annual_cost']:,.0f} scenario range</small></a>''' for c in sorted(data['cities'],key=lambda x:x['city']))
 body=f'''<header class="page-hero"><div class="wrap"><div class="eyebrow">City database</div><h1>Cold plunge conditions by city</h1><p class="lede">Each report contains three scenarios, target-temperature sensitivity, monthly detail, provenance and a downloadable JSON record.</p></div></header><main id="main" class="wrap"><section class="section"><div class="city-grid">{cards}</div></section></main>'''
 write('cities/index.html',page_shell('Cold Plunge Cost by U.S. City | ArcticSoak','Explore transparent cold plunge electricity and operating-cost scenarios by U.S. city.',body,'/cities/',data))

def state_slug(code):return STATE_NAMES.get(code,code).lower().replace(' ','-')
def generate_states(data):
 groups={}
 for city in data['cities']:groups.setdefault(city['state'],[]).append(city)
 cards=[]
 for state in sorted(groups,key=lambda s:STATE_NAMES.get(s,s)):
  cities=sorted(groups[state],key=lambda c:c['city']);slug=state_slug(state);average=sum(c['annual_cost'] for c in cities)/len(cities)
  cards.append(f'''<a class="city-card" href="/states/{slug}/"><span>{esc(state)} · {len(cities)} indexed cities</span><h2>{esc(STATE_NAMES.get(state,state))}</h2><p>{cities[0]['rate']*100:.1f}¢/kWh state input</p><small>${average:,.0f}/yr average reference estimate</small></a>''')
  rows=''.join(f'''<tr><th><a href="/cities/{c['slug']}/">{esc(c['city'])}</a></th><td>{esc(c['climate_status'])}</td><td>${c['scenarios']['efficient']['annual_cost']:,.0f}</td><td>${c['annual_cost']:,.0f}</td><td>${c['scenarios']['high_load']['annual_cost']:,.0f}</td></tr>''' for c in cities)
  body=f'''<header class="page-hero"><div class="wrap"><div class="crumb"><a href="/states/">States</a> / {esc(STATE_NAMES.get(state,state))}</div><div class="eyebrow">State electricity context</div><h1>Cold plunge costs in {esc(STATE_NAMES.get(state,state))}</h1><p class="lede">The current state residential electricity input is {cities[0]['rate']*100:.1f}¢/kWh ({esc(cities[0]['rate_period'])}). City climate profiles create the differences below.</p></div></header><main id="main" class="wrap"><section class="section"><div class="table-scroll"><table class="data-table"><thead><tr><th>City</th><th>Climate status</th><th>Efficient</th><th>Reference</th><th>High-load</th></tr></thead><tbody>{rows}</tbody></table></div></section><section class="article"><h2>What this state page can establish</h2><p>The electricity input is appropriately state-level because it comes from the EIA residential average, not a city utility tariff. City estimates should differ when station-specific climate profiles are available. Check your utility plan and equipment measurements before budgeting.</p></section></main>'''
  write(f'states/{slug}/index.html',page_shell(f"Cold Plunge Electricity Cost in {STATE_NAMES.get(state,state)} | ArcticSoak",f"State electricity-rate context and cold plunge operating-cost scenarios for indexed cities in {STATE_NAMES.get(state,state)}.",body,f'/states/{slug}/',data))
 body=f'''<header class="page-hero"><div class="wrap"><div class="eyebrow">State database</div><h1>Cold plunge electricity cost by state</h1><p class="lede">State pages match the geographic resolution of the EIA residential electricity input and link to climate-specific city reports.</p></div></header><main id="main" class="wrap"><section class="section"><div class="city-grid">{''.join(cards)}</div></section></main>'''
 write('states/index.html',page_shell('Cold Plunge Electricity Cost by State | ArcticSoak','State residential electricity rates and cold plunge planning scenarios for indexed U.S. cities.',body,'/states/',data))

def generate_data_portal(data):
 schema={'@context':'https://schema.org','@type':'DataCatalog','name':'ArcticSoak Open Cold Plunge Data','url':'https://arcticsoak.com/data/','dataset':{'@type':'Dataset','name':'U.S. Cold Plunge Climate & Cost Index','version':MODEL_VERSION,'dateModified':data['generated_at'],'license':'https://creativecommons.org/licenses/by/4.0/','distribution':[{'@type':'DataDownload','encodingFormat':'text/csv','contentUrl':'https://arcticsoak.com/data/cold-plunge-index.csv'},{'@type':'DataDownload','encodingFormat':'application/json','contentUrl':'https://arcticsoak.com/data/cold-plunge-index.json'}]}}
 body=f'''<header class="page-hero"><div class="wrap"><div class="eyebrow">Open data · {LICENSE}</div><h1>ArcticSoak data portal</h1><p class="lede">Download the current dataset, inspect city records and cite a permanent version with its model and source status intact.</p></div></header><main id="main" class="wrap"><section class="city-dashboard"><div class="score-card"><div class="reading-label">Current release</div><div class="hero-number">{esc(data['dataset_version'])}</div><p>Model {MODEL_VERSION} · {len(data['cities'])} cities · {data['verified_climate_records']} NOAA-verified climate records</p></div><div class="metrics"><div class="metric"><small>Generated UTC</small><strong class="small-value">{esc(data['generated_at'])}</strong></div><div class="metric"><small>License</small><strong>{LICENSE}</strong></div><div class="metric"><small>Electricity source</small><strong class="small-value">EIA when refreshed; fallback disclosed</strong></div><div class="metric"><small>Formats</small><strong>CSV · JSON</strong></div></div></section><section class="section"><div class="download-grid"><a class="download" href="/data/cold-plunge-index.csv"><span>CSV</span><strong>Current flat dataset</strong><small>Best for spreadsheets and analysis</small></a><a class="download" href="/data/cold-plunge-index.json"><span>JSON</span><strong>Current structured dataset</strong><small>Includes scenarios, months and provenance</small></a><a class="download" href="/data/releases/{esc(data['dataset_version'])}/cold-plunge-index.csv"><span>SNAPSHOT</span><strong>Permanent release CSV</strong><small>Dataset {esc(data['dataset_version'])}</small></a></div></section><section class="split"><div><h2>Core fields</h2><ul><li>Efficient, reference and high-load annual kWh and cost</li><li>Target sensitivity at 55°F, 50°F, 45°F and 39°F</li><li>Climate status and NOAA station when resolved</li><li>Electricity source, period, rate and geographic resolution</li><li>Model version and generated timestamp</li></ul></div><div><h2>Reuse terms</h2><p>Licensed under Creative Commons Attribution 4.0. You may share and adapt the dataset with appropriate credit, a license link and an indication of changes.</p><p><a href="https://creativecommons.org/licenses/by/4.0/">Read the CC BY 4.0 license</a></p></div></section><section class="section citation-box"><h2>Copyable dataset citation</h2><p>ArcticSoak. “U.S. Cold Plunge Climate &amp; Cost Index.” Dataset {esc(data['dataset_version'])}, model {MODEL_VERSION}. Generated {esc(data['generated_at'])}. https://arcticsoak.com/data/</p><button class="btn ghost" data-copy-citation>Copy citation</button></section></main><script src="/assets/app.js" defer></script>'''
 write('data/index.html',page_shell('ArcticSoak Open Cold Plunge Dataset | CSV & JSON','Download versioned cold plunge climate, energy and cost scenario data with provenance, licensing and citation guidance.',body,'/data/',data,schema))

def generate_research(data):
 cities=data['cities'];ordered=sorted(cities,key=lambda c:c['annual_cost']);median=sorted(c['annual_cost'] for c in cities)[len(cities)//2];low,high=ordered[0],ordered[-1];selection=ordered[:10]+list(reversed(ordered[-10:]))
 rows=''.join(f'''<tr><td>{i}</td><td><a href="/cities/{c['slug']}/">{esc(c['city'])}, {esc(c['state'])}</a></td><td>${c['annual_cost']:,.0f}</td><td>${c['scenarios']['efficient']['annual_cost']:,.0f}–${c['scenarios']['high_load']['annual_cost']:,.0f}</td><td>{esc(c['climate_status'])}</td></tr>''' for i,c in enumerate(selection,1))
 body=f'''<header class="page-hero"><div class="wrap"><div class="eyebrow">Annual report · Published {esc(data['generated_at'][:10])}</div><h1>2026 U.S. Cold Plunge Cost Index</h1><p class="lede">A transparent planning comparison across {len(cities)} cities—not a survey of measured appliances.</p></div></header><main id="main" class="wrap"><div class="notice warning"><strong>Evidence status:</strong> {data['verified_climate_records']} of {len(cities)} climate records are tied to retrieved NOAA normals. Findings involving provisional rows are hypotheses from the planning model, not verified city measurements.</div><section class="split stats"><div><small>Indexed-city median</small><strong>${median:,.0f}/yr</strong><p>Reference scenario</p></div><div><small>Lowest reference</small><strong>{esc(low['city'])}</strong><p>${low['annual_cost']:,.0f}/yr</p></div><div><small>Highest reference</small><strong>{esc(high['city'])}</strong><p>${high['annual_cost']:,.0f}/yr</p></div></section><section class="article wide"><h2>What changed in model 2.0</h2><p>The original index published one optimistic point estimate based on a constant 35-watt baseline, one conductance value and a fixed chiller COP. Model 2.0 publishes three setup scenarios, uses daily low/high profiles to estimate positive cooling degree-hours, reduces modeled COP in hotter conditions and includes explicit allowances for omitted loads.</p><p>The result is a planning range. It is not a statistical confidence interval and has not yet been calibrated against a large measured field dataset.</p><h2>The main finding</h2><p>Climate is not the only driver. A mild city can cost more than a hotter city when its state residential electricity rate is much higher. ArcticSoak therefore publishes physical energy beside dollar cost and labels the geographic resolution of both inputs.</p></section><section class="section"><h2>Current comparison table</h2><p>Ten lowest followed by ten highest reference estimates. Check status before quoting a city.</p><div class="table-scroll"><table class="data-table"><thead><tr><th>#</th><th>City</th><th>Reference</th><th>Range</th><th>Climate</th></tr></thead><tbody>{rows}</tbody></table></div></section><section class="citation-box"><h2>How to cite this report</h2><p>ArcticSoak. “2026 U.S. Cold Plunge Cost Index.” Model {MODEL_VERSION}, dataset {esc(data['dataset_version'])}. https://arcticsoak.com/research/2026-cold-plunge-cost-index/</p></section></main>'''
 write('research/2026-cold-plunge-cost-index/index.html',page_shell('2026 U.S. Cold Plunge Cost Index | ArcticSoak','Annual ArcticSoak report comparing cold plunge electricity and cost planning scenarios across U.S. cities.',body,'/research/2026-cold-plunge-cost-index/',data))
 index='''<header class="page-hero"><div class="wrap"><div class="eyebrow">ArcticSoak research</div><h1>Cold plunge energy research</h1><p class="lede">Versioned analysis, an open validation protocol and reports built from transparent data rather than generic product copy.</p></div></header><main id="main" class="wrap"><section class="section"><div class="tool-grid"><a class="tool" href="/research/2026-cold-plunge-cost-index/"><div class="num">ANNUAL INDEX</div><h3>2026 U.S. Cost Index</h3><p>National scenario comparison, evidence status and citable tables.</p></a><a class="tool" href="/research/model-validation/"><div class="num">OPEN PROTOCOL</div><h3>Model validation project</h3><p>How predicted-versus-metered performance will be collected and published.</p></a><a class="tool" href="/changelog/"><div class="num">VERSION HISTORY</div><h3>Model changelog</h3><p>What changed, why and how older citations should be interpreted.</p></a></div></section></main>'''
 write('research/index.html',page_shell('Cold Plunge Energy Research | ArcticSoak','ArcticSoak cold plunge energy studies, annual indexes and open model-validation protocol.',index,'/research/',data))

def write_datasets(data):
 cities=data['cities'];has_recent=any(c.get('recent_30d_avg_f') is not None for c in cities)
 headers=['rank_by_reference_cost','city','state','model_version','dataset_version','generated_at_utc','climate_status','climate_source','noaa_station_id','noaa_station_name','electricity_source','electricity_period','electricity_rate_per_kwh','rate_geography','arcticsoak_score','efficient_annual_kwh','efficient_annual_cost','reference_annual_kwh','reference_annual_cost','high_load_annual_kwh','high_load_annual_cost','reference_monthly_avg_cost','peak_month','cost_at_55f','cost_at_45f','cost_at_39f']
 if has_recent:headers+=['recent_30d_avg_f','recent_period_start','recent_period_end']
 csv_path=ROOT/'data/cold-plunge-index.csv'
 with open(csv_path,'w',newline='',encoding='utf-8') as f:
  writer=csv.DictWriter(f,fieldnames=headers);writer.writeheader()
  for c in sorted(cities,key=lambda x:x['cost_rank']):
   row={'rank_by_reference_cost':c['cost_rank'],'city':c['city'],'state':c['state'],'model_version':MODEL_VERSION,'dataset_version':data['dataset_version'],'generated_at_utc':data['generated_at'],'climate_status':c['climate_status'],'climate_source':c['climate_source'],'noaa_station_id':c.get('noaa_station_id') or '','noaa_station_name':c.get('noaa_station_name') or '','electricity_source':c['electricity_source'],'electricity_period':c['rate_period'],'electricity_rate_per_kwh':c['rate'],'rate_geography':c['rate_geography'],'arcticsoak_score':c['score'],'efficient_annual_kwh':c['scenarios']['efficient']['annual_kwh'],'efficient_annual_cost':c['scenarios']['efficient']['annual_cost'],'reference_annual_kwh':c['annual_kwh'],'reference_annual_cost':c['annual_cost'],'high_load_annual_kwh':c['scenarios']['high_load']['annual_kwh'],'high_load_annual_cost':c['scenarios']['high_load']['annual_cost'],'reference_monthly_avg_cost':c['scenarios']['reference']['monthly_average_cost'],'peak_month':c['peak_month'],'cost_at_55f':c['target_costs']['55'],'cost_at_45f':c['target_costs']['45'],'cost_at_39f':c['target_costs']['39']}
   if has_recent:row.update({'recent_30d_avg_f':c.get('recent_30d_avg_f') or '','recent_period_start':c.get('recent_period_start') or '','recent_period_end':c.get('recent_period_end') or ''})
   writer.writerow(row)
 write('data/cold-plunge-index.json',json.dumps(data,indent=2));release=ROOT/'data/releases'/data['dataset_version'];release.mkdir(parents=True,exist_ok=True)
 (release/'cold-plunge-index.csv').write_text(csv_path.read_text(encoding='utf-8'),encoding='utf-8');(release/'cold-plunge-index.json').write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
def generate_sitemap(data):
 paths=['/','/cities/','/states/','/rankings/','/data/','/research/','/research/2026-cold-plunge-cost-index/','/research/model-validation/','/calculators/ice/','/calculators/chiller/','/calculators/cost/','/calculators/ice-vs-chiller/','/methodology/','/about/','/editorial-standards/','/corrections/','/changelog/','/recommended-retailer/','/embed/city/']
 paths += [f"/cities/{c['slug']}/" for c in data['cities']]+[f"/states/{s}/" for s in sorted({state_slug(c['state']) for c in data['cities']})]
 last=data['generated_at'][:10];xml='<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'+''.join(f'  <url><loc>https://arcticsoak.com{p}</loc><lastmod>{last}</lastmod></url>\n' for p in paths)+'</urlset>\n';(ROOT/'sitemap.xml').write_text(xml,encoding='utf-8')
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--offline',action='store_true');parser.add_argument('--skip-recent',action='store_true');args=parser.parse_args()
 now=datetime.now(timezone.utc).replace(microsecond=0);config=load_cities();old=load_existing();old_by={c.get('slug'):c for c in old.get('cities',[])};rates=load_seed_rates()
 if not args.offline:
  try:
   fresh=fetch_eia_rates(os.getenv('EIA_API_KEY'))
   if fresh:rates.update(fresh)
  except Exception as exc:print('EIA update failed; retained disclosed fallback rates:',exc)
 def task(row):return compute_city(row,rates.get(row['state'],{'rate':.16,'period':'fallback','source':'Fallback planning rate'}),old_by.get(row['slug'],{}),args.offline,args.skip_recent)
 results=[]
 if args.offline:
  for i,row in enumerate(config,1):results.append(task(row));print(f"[{i}/{len(config)}] {row['slug']}")
 else:
  with ThreadPoolExecutor(max_workers=4) as pool:
   futures={pool.submit(task,row):row for row in config}
   for i,future in enumerate(as_completed(futures),1):
    row=futures[future]
    try:results.append(future.result());print(f"[{i}/{len(config)}] {row['slug']}")
    except Exception as exc:print('City update failed',row['slug'],exc);results.append(compute_city(row,rates.get(row['state'],{'rate':.16,'period':'fallback','source':'Fallback planning rate'}),old_by.get(row['slug'],{}),True,True))
 results.sort(key=lambda c:c['city'])
 for rank,c in enumerate(sorted(results,key=lambda c:c['annual_cost']),1):c['cost_rank']=rank
 for rank,c in enumerate(sorted(results,key=lambda c:c['score'],reverse=True),1):c['score_rank']=rank
 verified=sum(c['climate_status']=='verified' for c in results);data={'dataset_version':now.strftime('%Y-%m-%d'),'model_version':MODEL_VERSION,'generated_at':now.isoformat(),'license':LICENSE,'climate_vintage':CLIMATE_VINTAGE,'verified_climate_records':verified,'reference_model':{'target_f':TARGET_F,'gallons':GALLONS,'scenarios':SCENARIOS,'method':'Monthly normal low/high converted to positive cooling degree-hours; ambient-adjusted COP; explicit daily allowances'},'source_note':'Every city identifies whether climate is NOAA-verified or provisional. Electricity inputs identify EIA versus fallback status and remain state-level.','cities':results}
 write('data/index.json',json.dumps(data,indent=2));write_datasets(data);median=sorted(c['annual_cost'] for c in results)[len(results)//2]
 for c in results:generate_city(c,data,median)
 generate_home(data);generate_rankings(data);generate_cities_index(data);generate_states(data);generate_data_portal(data);generate_research(data);generate_sitemap(data)
 print(f"Generated {len(results)} city pages and dataset {data['dataset_version']}")
if __name__=='__main__':main()
