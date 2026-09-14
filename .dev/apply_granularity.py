"""Apply the reviewed granularity change to the pinned development worktree."""
from pathlib import Path
root=Path('.')
def edit(name, fn):
    p=root/name
    old=p.read_text()
    new=fn(old)
    assert new!=old,name
    p.write_text(new)

def domain(s):
    return s.replace('LEVELS = {"raw": 0, "30s": 30, "1m": 60, "5m": 300, "1h": 3600, "1d": 86400}', '''# Ordered presets for Auto and reusable market-wide caches.
LEVELS = {"raw": 0, "30s": 30, "1m": 60, "2m": 120, "5m": 300,
          "10m": 600, "15m": 900, "30m": 1800, "1h": 3600,
          "4h": 14400, "1d": 86400}
MAX_CUSTOM_MINUTES = 1440


def normalize_level(value: str, *, allow_auto: bool = False) -> str:
    """Validate granularity before SQL/path use; canonicalize equivalent presets.

    Custom intervals are whole minutes (1..1440), aligned to Unix epoch UTC.
    Manual intervals never fall back silently to Auto or another resolution.
    """
    if value == "auto" and allow_auto:
        return value
    if isinstance(value, str) and value in LEVELS:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,3}m", value):
        minutes = int(value[:-1])
        if minutes <= MAX_CUSTOM_MINUTES:
            seconds = minutes * 60
            return next((name for name, size in LEVELS.items() if size == seconds), value)
    raise ValueError("Unknown resolution. Choose Auto, raw, 30s, a preset, or 1–1440 whole minutes (e.g. 2m or 7m).")


def level_seconds(value: str) -> int:
    value = normalize_level(value)
    return LEVELS[value] if value in LEVELS else int(value[:-1]) * 60
''')
edit('xvi/domain.py',domain)

def data(s):
    s=s.replace('parse_wallets, wallet_predicate','parse_wallets, wallet_predicate, normalize_level, level_seconds',1)
    s=s.replace('        addresses = parse_wallets(wallets)\n','        level = normalize_level(level, allow_auto=True)\n        addresses = parse_wallets(wallets)\n',1)
    s=s.replace('        if level not in {"auto",*LEVELS}:\n            raise ValueError("Unknown resolution")\n','')
    s=s.replace('        elif addresses:\n            # Cached bars describe the whole market. Wallet subsets must be\n            # aggregated from their actual raw fills, never filtered after bars.\n            seconds = LEVELS[selected]', '''        elif addresses or selected not in LEVELS:
            # Wallet subsets and custom intervals are recomputed exactly from
            # selected raw fills. Do not create an archive-sized cache per custom
            # interval, or use market-wide bars for a wallet subset.
            seconds = level_seconds(selected)''')
    s=s.replace('bin_seconds=LEVELS[selected]','bin_seconds=level_seconds(selected)')
    s=s.replace('    if seconds not in LEVELS.values() or not seconds:\n        raise ValueError("Invalid aggregation interval")','    if type(seconds) is not int or not (seconds == 30 or (60 <= seconds <= 86400 and seconds % 60 == 0)):\n        raise ValueError("Invalid aggregation interval: use 30 seconds or 1–1440 whole minutes")')
    return s
edit('xvi/data.py',data)

def api(s):
    s=s.replace('from .domain import LEVELS, clean_json','from .domain import clean_json')
    s=s.replace('parse_wallets, wallet_predicate','parse_wallets, wallet_predicate, normalize_level',1)
    s=s.replace('        if level not in LEVELS or end<=start:\n','        level = normalize_level(level)\n        if end<=start:\n')
    return s
edit('xvi/app.py',api)

def filters(s):
    start=s.index('  const helpers =')
    s=s[:start]+'''  // Same canonical interval names as the API. Custom input is whole minutes.
  const PRESETS = {auto: null, raw: 0, '30s': 30, '1m': 60, '2m': 120,
    '5m': 300, '10m': 600, '15m': 900, '30m': 1800, '1h': 3600, '4h': 14400, '1d': 86400};
  function normalizeLevel(value) {
    if (Object.hasOwn(PRESETS, value)) return value;
    if (/^[1-9][0-9]{0,3}m$/.test(value || '')) {
      const minutes = Number(value.slice(0, -1));
      if (minutes <= 1440) return Object.keys(PRESETS).find(key => PRESETS[key] === minutes * 60) || value;
    }
    throw new Error('Choose a granularity preset or 1–1440 whole minutes.');
  }
  function granularity(choice, minutes = '') {
    if (choice !== 'custom') return normalizeLevel(choice);
    const text = String(minutes).trim();
    if (!/^[1-9][0-9]{0,3}$/.test(text) || Number(text) > 1440) {
      throw new Error('Custom granularity must be a whole number of minutes from 1 to 1440.');
    }
    return normalizeLevel(text + 'm');
  }
  function granularityControls(level) {
    level = normalizeLevel(level);
    return Object.hasOwn(PRESETS, level) ? {choice: level, minutes: ''} : {choice: 'custom', minutes: level.slice(0, -1)};
  }
'''+s[start:]
    return s.replace('const helpers = {dateWindow, displayDates, parseWallets};','const helpers = {dateWindow, displayDates, parseWallets, normalizeLevel, granularity, granularityControls};')
edit('xvi/static/filters.js',filters)

def html(s):
    s=s.replace('Choose your dates. Optionally narrow to specific wallets.','Choose dates and granularity. Optionally narrow to specific wallets.')
    s=s.replace('class="filter-field">Initial date','class="filter-field start-field">Initial date')
    s=s.replace('class="filter-field">Final date','class="filter-field end-field">Final date')
    control='''<div class="filter-field granularity-field"><label for="level">Granularity <span>chart bars</span></label>
<select id="level" name="granularity" aria-describedby="granularity-note"><option value="auto">Auto (zoom-aware)</option><option value="raw">Raw fills</option><option value="30s">30 seconds</option><option value="1m">1 minute</option><option value="2m">2 minutes</option><option value="5m">5 minutes</option><option value="10m">10 minutes</option><option value="15m">15 minutes</option><option value="30m">30 minutes</option><option value="1h">1 hour</option><option value="4h">4 hours</option><option value="1d">1 day</option><option value="custom">Custom minutes…</option></select>
<div id="custom-granularity" class="custom-granularity" hidden><label for="granularity-minutes" class="sr-only">Custom interval in minutes</label><input id="granularity-minutes" name="granularity-minutes" type="number" min="1" max="1440" step="1" value="2" inputmode="numeric" aria-describedby="granularity-note"><span>minutes</span></div></div>
'''
    s=s.replace('<label class="filter-field wallet-field">',control+'<label class="filter-field wallet-field">',1)
    s=s.replace('<p id="wallet-note">Separate addresses with commas, spaces, or new lines. A fill is counted once.</p>','<p id="wallet-note">Separate addresses with commas, spaces, or new lines. A fill is counted once.</p><p id="granularity-note">Granularity groups the chart and volume. The trade table and CSV keep individual fills.</p>')
    start=s.index('<div class="chart-toolbar">')
    end=s.index('\n',start)
    return s[:start]+s[end+1:]
edit('xvi/static/index.html',html)

def css(s):
    s=s.replace('grid-template-columns:minmax(145px,1fr) minmax(145px,1fr) minmax(220px,2fr) minmax(135px,.9fr);gap:13px','grid-template-columns:minmax(135px,1fr) minmax(135px,1fr) minmax(155px,1.1fr) minmax(190px,1.65fr) minmax(130px,.95fr);grid-template-areas:"start end granularity wallets role";gap:13px')
    s=s.replace('.filter-field{','.start-field{grid-area:start}.end-field{grid-area:end}.granularity-field{grid-area:granularity}.wallet-field{grid-area:wallets}.role-field{grid-area:role}\n.filter-field{',1)
    s=s.replace('.filter-field>span{','.filter-field>span,.granularity-field>label>span{')
    return s[:s.index('@media(max-width:1250px)')]+'''.custom-granularity{display:flex;align-items:center;gap:9px;margin-top:8px}
.custom-granularity[hidden]{display:none}
.filter-field .custom-granularity input{margin-top:0;width:85px;flex:1}
.custom-granularity>span{font-size:10px;color:var(--muted);font-weight:400}
@media(max-width:1250px){.filter-grid{grid-template-columns:minmax(0,1fr) minmax(0,1fr);grid-template-areas:"start end" "granularity role" "wallets wallets"}.filters-panel{padding:18px}.filter-footer{align-items:flex-start}.filter-actions{gap:10px}}
@media(max-width:780px){.filters-panel{padding:16px;margin-bottom:15px}.filter-heading h2{font-size:12px}.filter-heading p{font-size:9px}.filter-heading .tag{font-size:8px}.filter-grid{gap:14px 10px;grid-template-areas:"start end" "granularity granularity" "wallets wallets" "role role"}.filter-field{font-size:10px}.filter-field>span,.granularity-field>label>span{font-size:8px}.filter-field input,.filter-field select{font-size:12px;padding:9px 8px}.wallet-field textarea{font-size:12px}.filter-footer{flex-direction:column;gap:12px}.filter-actions{width:100%;justify-content:flex-end;gap:17px}.filter-actions .primary{font-size:11px}.filter-notes p{font-size:9px}.filter-scope{font-size:10px}.filter-field input::-webkit-date-and-time-value{text-align:left}}
'''
edit('xvi/static/filters.css',css)

def js(s):
    s=s.replace('const {dateWindow,displayDates,parseWallets}','const {dateWindow,displayDates,parseWallets,normalizeLevel,granularity,granularityControls}')
    s=s.replace("walletRole:'either',dateDirty:false","walletRole:'either',dateDirty:false,level:'auto'")
    s=s.replace("['range-start','range-end','wallet-ids'])","['range-start','range-end','wallet-ids','level','granularity-minutes'])",1)
    i=s.index('  function syncDates(data){')
    s=s[:i]+'''  function toggleCustomGranularity(){
    const custom=$('level').value==='custom';
    $('custom-granularity').hidden=!custom;$('granularity-minutes').disabled=!custom;
    clearFilterError();
  }
  function setGranularityControls(level){
    const controls=granularityControls(level);
    $('level').value=controls.choice;
    if(controls.minutes)$('granularity-minutes').value=controls.minutes;
    toggleCustomGranularity();
  }
'''+s[i:]
    s=s.replace('    let dates,wallets;','    let dates,wallets,level;')
    i=s.index('    if(state.dateDirty||!state.series){')
    s=s[:i]+'''    try{level=granularity($('level').value,$('granularity-minutes').value);}
    catch(error){$('level').setAttribute('aria-invalid','true');$('granularity-minutes').setAttribute('aria-invalid','true');$('filter-error').textContent=error.message;$('filter-error').hidden=false;return;}
'''+s[i:]
    s=s.replace('state.wallets=wallets;state.walletRole=role;state.offset=0;','state.wallets=wallets;state.walletRole=role;state.level=level;state.offset=0;')
    s=s.replace("    $('level').value='auto';await loadSeries(true);","    state.level=normalizeLevel(initialFilters?.level||'auto');setGranularityControls(state.level);await loadSeries(true);")
    s=s.replace("...walletQuery(),level:$('level').value,","...walletQuery(),level:state.level,")
    s=s.replace("...walletQuery()}));}","...walletQuery(),...(state.level!=='auto'?{level:state.level}:{})}));}",1)
    s=s.replace("state.wallets=[];state.walletRole='either';$('wallet-ids').value='';$('wallet-role').value='either';renderFilters();chart.overview=null;","state.wallets=[];state.walletRole='either';state.level='auto';setGranularityControls(state.level);$('wallet-ids').value='';$('wallet-role').value='either';renderFilters();chart.overview=null;")
    s=s.replace("$('level').addEventListener('change',()=>{state.offset=0;loadSeries();});","$('level').addEventListener('change',toggleCustomGranularity);\n  $('granularity-minutes').addEventListener('input',clearFilterError);")
    s=s.replace('null,{wallets,role});}',"null,{wallets,role,level:normalizeLevel(hash.get('level')||'auto')});}")
    return s
edit('xvi/static/app.js',js)

# Verify that the output is byte-identical to the locally tested implementation.
import subprocess
expected={
 'xvi/app.py':'24531cf44dcbda5c3e60862607e69573205a9e5d',
 'xvi/data.py':'7cfcff4bdbd5db6030e86c92c38b0e37b92f9728',
 'xvi/domain.py':'79d86c8d153f828bef5e85dad7c8555788fbcdc0',
 'xvi/static/app.js':'2106711f8f627b385da7c0f5cc856f23c5ee5bcc',
 'xvi/static/filters.css':'d1c877b00feb368b2b090df8d8458fca911bc1e9',
 'xvi/static/filters.js':'423de69bc488bd4d59e5081e9314e04bc8efd1e5',
 'xvi/static/index.html':'42b514f1f9bf11f7a9b30b0402c78b66a855cc3a'
}
for path,sha in expected.items():
    assert subprocess.check_output(['git','hash-object',path],text=True).strip()==sha,path
