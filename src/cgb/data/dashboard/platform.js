/* HOL: Context Graph Benchmark. Local assets, text-only artifacts, no vendor UI runtime. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const snapshot = window.CGB_PLATFORM_SNAPSHOT;
  let state, selectedRun, selectedOverall, selectedAssurance, pendingRun, initialized = false, refreshInFlight = false, busy = false;
  const colors = ['#95abff','#4ed6b1','#f4bb70','#e5a0ef','#82c8ed','#e8a5ba','#afc479','#78ddd9','#efad7c'];
  const componentColor = i => colors[i % colors.length];
  const number = (x, digits=1) => typeof x === 'number' && Number.isFinite(x) ? x.toLocaleString(undefined,{maximumFractionDigits:digits,minimumFractionDigits:digits}) : '—';
  const median = value => value?.p50 ?? value?.median;
  const pct = x => typeof x === 'number' ? number(x) + '%' : '—';
  const time = x => {if(typeof x !== 'number')return '—';const n=Math.max(0,Math.floor(x));return n>=3600?Math.floor(n/3600)+'h '+Math.floor(n%3600/60)+'m':n>=60?Math.floor(n/60)+'m '+n%60+'s':n+'s';};
  const when = x => {const d=new Date(x);return Number.isNaN(d.getTime())?'Unrecorded time':d.toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});};
  function el(tag, attributes={}, children=[]) {
    const node=document.createElement(tag);
    for(const [key,value] of Object.entries(attributes)) {
      if(value===null||value===undefined)continue;
      if(key==='text')node.textContent=String(value);
      else if(key==='class')node.className=value;
      else if(key==='onClick')node.addEventListener('click',value);
      else if(key==='style')Object.assign(node.style,value);
      else if(key==='disabled')node.disabled=Boolean(value);
      else node.setAttribute(key,String(value));
    }
    for(const child of children)if(child!==null&&child!==undefined)node.append(child.nodeType?child:document.createTextNode(String(child)));
    return node;
  }
  const tag=(text,kind='')=>el('span',{class:'tag '+kind,text});
  const button=(text,handler,kind='subtle')=>el('button',{type:'button',class:'button '+kind,text,onClick:handler});
  const engine=id=>state.engines.find(e=>e.id===id||e.id===id?.replace(/-local$/,''))||{name:id,color:'#b4c2df'};
  const color=id=>/^#[a-f0-9]{6}$/i.test(engine(id).color||'')?engine(id).color:'#b4c2df';
  const engineName=id=>engine(id).name;
  function empty(title,description,action=true) {
    return el('div',{class:'panel empty'},[el('div',{class:'empty-symbol','aria-hidden':'true',text:'⬡'}),el('h3',{text:title}),el('p',{text:description}),action?el('a',{href:'#studio',class:'button primary',text:'Open Run Studio →'}):null]);
  }
  function showDialog(title,children) {
    $('detail-title').textContent=title;
    $('detail-body').replaceChildren(...children);
    $('detail-dialog').showModal();
  }
  function details(run) {
    const commands=[];
    if(run.schema==='cgb.platform-comparison.v1')commands.push('cgb platform replay '+run.path);
    if(run.schema==='cgb.portable-soak-state.v1')commands.push('cgb platform soak-replay '+run.path);
    if(run.schema==='cgb.assurance-state.v1')commands.push('cgb test-pack replay '+run.path);
    if(run.schema==='cgb.portable-macro-state.v1')commands.push('cgb platform study-replay '+run.path);
    const children=[el('p',{text:'This run retains its original observations and named configurations. A completed run is development evidence; it is not independent comparative qualification.'}),el('p',{class:'field-help',text:'Replay requires the recorded evaluator revision. A newer installation intentionally rejects a different harness digest.'})];
    if(commands.length)children.push(el('div',{class:'code-box'},[el('code',{text:commands.join('\n')})]));
    if(!snapshot&&run.path)children.push(button('Read retained result',()=>artifact(run.path+'/result.json')),button('Read frozen registration',()=>artifact(run.path+(['cgb.platform-comparison.v1','cgb.portable-macro-state.v1'].includes(run.schema)?'/registration.json':'/plan.json'))));
    children.push(el('pre',{text:JSON.stringify(run,null,2)}));
    showDialog(run.name||run.id,children);
  }
  async function artifact(path) {
    try {
      const response=await fetch('/api/artifact?path='+encodeURIComponent(path),{cache:'no-store'});
      const text=await response.text();
      if(!response.ok)throw Error(text);
      showDialog('Retained artifact',[el('p',{class:'mono',text:path}),el('pre',{text})]);
    }catch(error){showDialog('Artifact unavailable',[el('p',{text:String(error.message||error)})]);}
  }
  function kpis() {
    $('platform-version').textContent=(state.version||'Recorded evaluator')+' · Apache-2.0';
    const complete=state.comparisons.filter(r=>r.status==='completed');
    const costs=state.answer_metrics||{status:'unavailable',measured_results:0,system_results:0};
    const costLabel=({available:'Measured',partial:'Partial',unavailable:'Not measured'})[costs.status]||'Not measured';
    const entries=[['Local engines ready',state.engines.filter(e=>e.available).length+' / '+state.engines.length,'Explicit runtime bindings'],
      ['Local comparisons',String(complete.length),'Every attempt retained'],
      ['Local answer token metrics',costLabel,costs.system_results?costs.measured_results+' / '+costs.system_results+' recorded system results have routine/paired answer cost':'No measured answer cost; context tokens stay separate'],
      ['Endurance profiles','1h / 24h','12 persistent agent lanes per engine']];
    $('platform-kpis').replaceChildren(...entries.map(([label,value,note])=>el('article',{class:'kpi'},[el('div',{class:'label',text:label}),el('div',{class:'value '+(value.length>12?'muted':''),text:value}),el('div',{class:'note',text:note})])));
  }
  function table(headers,rows) {
    return el('div',{class:'table-wrap'},[el('table',{},[el('thead',{},[el('tr',{},headers.map(t=>el('th',{scope:'col',text:t})))]),el('tbody',{},rows.map(cells=>el('tr',{},cells.map(c=>el('td',{},[c])))))])]);
  }
  function policyOverview() {
    const dimensions=state.policy.dimensions;
    const weight=id=>dimensions.find(d=>d.id===id)?.weight;
    $('current-policy-heading').textContent=dimensions.length+' dimensions. Every test matters.';
    $('policy-summary').textContent='Reliability '+pct(100*weight('reliability'))+' · Token efficiency '+pct(100*weight('tokens'))+' · Security '+pct(100*weight('security'))+' · Privacy '+pct(100*weight('privacy'));
    $('current-policy-note').textContent='Current policy · '+$('policy-summary').textContent+'. All nine factors are required for an overall score.';
    $('policy-identity').textContent=state.policy.id+' · weighted arithmetic mean';
    $('policy-allocation').replaceChildren(...dimensions.map((d,i)=>el('span',{style:{flexGrow:d.weight,background:componentColor(i)},title:d.label+': '+pct(100*d.weight)})));
    $('policy-allocation').setAttribute('aria-label',dimensions.map(d=>d.label+' '+pct(100*d.weight)).join('; '));
    $('policy-components').replaceChildren(...dimensions.map((d,i)=>el('article',{class:'policy-component'},[el('div',{},[el('span',{class:'engine-dot',style:{background:componentColor(i)}}),el('strong',{text:pct(100*d.weight)})]),el('span',{text:d.label})])));
  }
  function scoreTable(study) {
    const dimensions = study.policy.dimensions;
    const headers = ['System', 'Overall', 'Coverage', ...dimensions.map(d => d.label + ' · ' + pct(100 * d.weight))];
    const rows = study.systems.map(system => [
      system.engine ? nameCell(system) : system.id,
      system.score == null ? system.status : number(system.score, 2),
      pct(system.coverage_percent),
      ...dimensions.map(d => system.components[d.id] == null ? 'Not measured' : number(system.components[d.id]))
    ]);
    return table(headers, rows);
  }
  function referenceStudy() {
    const study = state.reference_study;
    $('reference-study').hidden = !study;
    if (!study || $('reference-content').dataset.id === study.id) return;
    const systems = study.systems;
    const dimensions = study.policy.dimensions;
    const summary = table(['System', 'Overall / 100', 'Coverage', 'Tokens per safe correct answer'],
      systems.map(s => [nameCell(s), s.score == null ? s.status : number(s.score, 2), pct(s.coverage_percent),
        number(s.tokens_per_safe_correct_answer, 2)]));
    const components = table(['Component', 'Weight', ...systems.map(s => engineName(s.engine))],
      dimensions.map(d => [d.label, pct(100 * d.weight), ...systems.map(s => number(s.components[d.id], 2))]));
    $('reference-content').replaceChildren(el('article', {class:'panel'}, [
      el('p', {text:'Each system ran 138 query cells and a one-hour soak on the same macOS host with ' + study.reader.model + '.'}),
      summary,
      el('details', {open:'open', class:'reference-components'}, [el('summary',{text:'Scores by component'}), components]),
      el('p', {class:'field-help', text:'Multiply each component score by its weight and add the contributions. The table rounds values for display.'}),
      el('a', {href:'#methodology',class:'text-link',text:'Explore the scoring formula and weights →'}),
      el('p', {class:'field-help', text:study.disclosure + ' Study ' + study.id + ' · ' + study.metric}),
      button('View recorded result', () => showDialog('Reference study result', [el('pre', {text:JSON.stringify(study, null, 2)})]))
    ]));
    $('reference-content').dataset.id = study.id;
  }
  function overallStudies() {
    referenceStudy();
    const studies=state.overall_studies||[],select=$('overall-select');
    $('recorded-overall').hidden=!studies.length;
    const previous=selectedOverall||select.value;
    select.replaceChildren(...studies.map(s=>el('option',{value:s.id,text:s.id+' · '+s.metric})));
    selectedOverall=studies.some(s=>s.id===previous)?previous:studies[0]?.id;
    select.disabled=!studies.length;
    if(!studies.length){select.replaceChildren(el('option',{text:'No sealed submission yet'}));$('overall-content').replaceChildren(el('article',{class:'panel'},[el('h3',{text:'Complete evidence before a headline score.'}),el('p',{class:'field-help',text:'The current policy requires nine measured components. Run a complete benchmark study to bind retrieval, graph and an assurance-enabled endurance cohort. Standalone pairs and pilots retain their own results and cannot fill an endurance requirement.'}),el('a',{href:'#methodology',class:'text-link',text:'See the weights, requirements and interactive score →'})]));return;}
    select.value=selectedOverall;
    const study=studies.find(s=>s.id===selectedOverall);
    $('overall-content').replaceChildren(el('article',{class:'panel'},[el('div',{class:'result-meta'},[el('span',{class:'run-name',text:study.id}),tag(study.metric),tag(study.evidence_class)]),el('p',{class:'field-help',text:study.verification}),scoreTable(study),el('p',{class:'field-help',text:study.disclosure}),button('Submission evidence ↗',()=>snapshot?showDialog('Recorded submission',[el('pre',{text:JSON.stringify(study,null,2)})]):artifact(study.path+'/result.json'))]));
  }
  function nameCell(row) {
    const identity=[row.version||'runtime-described',row.id&&row.id!==row.engine?row.id:null].filter(Boolean).join(' · ');
    return el('div',{class:'engine-cell'},[el('span',{class:'engine-dot',style:{background:color(row.engine)}}),el('div',{},[el('strong',{text:engineName(row.engine)}),el('span',{class:'engine-sub',text:identity})])]);
  }
  function bar(label,value,kind='') {
    const numeric=typeof value==='number'&&Number.isFinite(value)?Math.max(0,Math.min(100,value)):0;
    return el('div',{class:'bar-track',role:'img','aria-label':label+': '+pct(value)},[el('div',{class:'bar-fill '+kind,style:{width:numeric+'%'}})]);
  }
  function chart(title,caption,children,legend=false) {
    return el('div',{class:'chart-card'},[el('div',{class:'chart-header'},[el('h3',{text:title}),el('span',{text:caption})]),legend?el('div',{class:'chart-legend'},[el('span',{},[el('i',{class:'legend-dot'}),'Evidence']),el('span',{},[el('i',{class:'legend-dot graph'}),'Graph'])]):null,...children]);
  }
  function comparisons() {
    const options=state.comparisons.filter(r=>r.rows?.length).sort((a,b)=>String(b.started_at||'').localeCompare(String(a.started_at||'')));
    const select=$('comparison-select');
    const old=selectedRun||select.value;
    select.replaceChildren(...options.map(r=>el('option',{value:r.id,text:(r.name||r.id)+' · '+when(r.started_at)})));
    if(pendingRun&&options.some(r=>r.id===pendingRun)){selectedRun=pendingRun;pendingRun=null;}else selectedRun=options.some(r=>r.id===old)?old:options[0]?.id;
    select.disabled=!options.length;
    if(selectedRun)select.value=selectedRun;
    if(!options.length){select.replaceChildren(el('option',{text:'No comparison results yet'}));$('comparison-content').replaceChildren(empty('Make your first comparison.','Run the included development fixtures against installed engines. This view will show actual component scores and query timing.'));return;}
    const run=options.find(r=>r.id===selectedRun),rows=run.rows||[];
    const label=row=>engineName(row.engine)+(rows.filter(r=>r.engine===row.engine).length>1?' · '+row.id:'');
    const quality=rows.map(row=>el('div',{class:'bar-row'},[el('span',{class:'bar-label',text:label(row),title:label(row)}),el('div',{class:'bar-stack'},[bar('Evidence',row.profiles?.evidence?.score),bar('Graph',row.profiles?.graph?.score,'graph')]),el('div',{class:'bar-value'},[el('div',{text:number(row.profiles?.evidence?.score)}),el('div',{text:number(row.profiles?.graph?.score)})])]));
    const max=Math.max(1,...rows.map(r=>median(r.query_ms)||0));
    const timing=rows.map(row=>el('div',{class:'bar-row'},[el('span',{class:'bar-label',text:label(row),title:label(row)}),el('div',{class:'bar-track'},[el('div',{class:'bar-fill',style:{width:Math.max(1,100*(median(row.query_ms)||0)/max)+'%',background:color(row.engine)}})]),el('span',{class:'bar-value',text:number(median(row.query_ms))})]));
    const panel=el('article',{class:'panel'},[el('div',{class:'result-meta'},[el('span',{class:'run-name',text:run.name||run.id}),tag(run.status,run.status==='completed'?'good':'warning'),tag('Evidence and graph query tracks')]),el('div',{class:'charts-grid'},[chart('Evidence & graph quality','0–100 · higher is better',quality,true),chart('Median query response','ms · lower is better',[...timing,el('p',{class:'chart-caption',text:'Includes adapter work inside query. Process startup is recorded separately. Rebuild and cache treatments remain part of each named adapter.'})])]),
      table(['Engine / version','Evidence','Graph','Query p50','Query p95','Violations','Failures','Attempts'],rows.map(r=>[nameCell(r),pct(r.profiles?.evidence?.score),pct(r.profiles?.graph?.score),number(median(r.query_ms))+' ms',number(r.query_ms?.p95)+' ms',String(r.violations),String(r.failures),String(r.attempts)])),
      el('div',{class:'result-foot'},[el('span',{text:'Budgets: '+run.budgets.join(' / ')+' · '+run.replicates+' replicate(s) · replay '+run.replay}),button('Evidence & configuration ↗',()=>details(run))]),
      el('p',{class:'field-help',text:(rows[0]?.profiles?.evidence?.evidence_class==='smoke'?'Evidence: smoke fixture. ':'Evidence: '+(rows[0]?.profiles?.evidence?.suite_id||'retained development fixture')+'. ')+'Graph: public synthetic development tasks, when included. These are separate component metrics. Answer, token and native workflow tests are not implied by this run.'})]);
    if(run.overall_policy){
      const recorded = state.policy.id === run.overall_policy ? state.policy :
        state.overall_studies?.find(study => study.metric === run.overall_policy)?.policy;
      if(recorded)panel.append(el('details',{class:'coverage-details'},[el('summary',{text:'Overall component coverage · '+recorded.id}),el('p',{class:'field-help',text:'Only complete fixed-budget component reductions appear here. The Evidence/Graph charts above retain their track-average definitions. Missing model, endurance and adversarial tracks are not filled from these query results.'}),table(['Engine',...recorded.dimensions.map(d=>d.label)],rows.map(r=>[engineName(r.engine),...recorded.dimensions.map(d=>typeof r.overall_components?.[d.id]==='number'?number(r.overall_components[d.id]):'Not measured')])),el('p',{class:'field-help',text:'Headline status: '+[...new Set(rows.map(r=>r.overall_status||'incomplete'))].join(', ')+'. Critical violations take precedence over incomplete evidence.'})]));
    }
    $('comparison-content').replaceChildren(panel);
  }
  function soaks() {
    if(!state.soaks.length){$('soak-content').replaceChildren(el('div',{class:'panel'},[el('h3',{text:'Ready when you are.'}),el('p',{class:'field-help',text:'Both endurance schedules are available in Run Studio. Start with a diagnostic pilot. Real model token and answer metrics require a bound reader; they are not replaced with estimates.'})]));return;}
    $('soak-content').replaceChildren(...state.soaks.slice(0,5).map(run=>{
      const progress=run.active_engine?Math.min(100,100*run.engine_elapsed_seconds/run.seconds_per_engine):run.status==='completed'?100:0;
      const live=run.live?.[run.active_engine];
      const elements=[el('div',{class:'soak-run-heading'},[el('div',{},[el('h3',{text:run.name||run.id}),el('small',{text:when(run.started_at)+' · '+(run.reader?run.reader.model+' @ '+run.reader.revision:'No reader · context metrics only')})]),tag(run.pilot?'Diagnostic pilot':run.hours+'h per engine',run.pilot?'warning':'')])];
      if(run.active_engine){elements.push(el('div',{class:'progress-label'},[el('span',{text:engineName(run.active_engine)+' · '+run.status}),el('span',{text:time(run.engine_elapsed_seconds)+' / '+time(run.seconds_per_engine)})]),el('progress',{value:progress,max:100,'aria-label':'Current engine soak progress'}));}
      if(live)elements.push(el('div',{class:'live-metrics'},[['Observed cycles',String(live.attempts)],['Correct cycles',String(live.correct)],['Rolling query p50',number(live.query_ms_rolling?.p50)+' ms'],['Context violations',String(live.violations)]].map(([k,v])=>el('span',{},[k,el('strong',{text:v})]))));
      if(run.rows?.length)elements.push(table(['Engine','Cycles correct','Query p50','Answer accuracy','Tokens / correct','Tokens saved / correct','Security','Privacy','Duration'],run.rows.map(r=>[nameCell(r),pct(r.cycle_success_percent),number(median(r.query_ms))+' ms',pct(r.answer_accuracy_percent),number(r.model_tokens_per_correct_answer?.value),number(r.tokens_saved_per_correct_answer),pct(r.assurance?.components.security),pct(r.assurance?.components.privacy),r.duration_complete?(run.pilot?'Pilot complete':run.hours+'h complete'):'Incomplete'])));
      elements.push(el('div',{class:'result-foot'},[el('span',{text:run.status+' · '+run.engines.map(engineName).join(' / ')}),button('Inspect soak evidence ↗',()=>details(run))]));
      return el('article',{class:'panel'},elements);
    }));
  }
  function assuranceResults(){
    const runs=state.assurance||[],select=$('assurance-select');
    if(!runs.length){select.replaceChildren(el('option',{text:'No paired run yet'}));select.disabled=true;$('assurance-content').replaceChildren(empty('Test instruction and data boundaries.','Run the eight core case pairs with an explicit local model. Results include safe utility, attacker outcomes, disclosure gates and tokens per safe correct answer.'));return;}
    const previous=selectedAssurance||select.value;select.disabled=false;select.replaceChildren(...runs.map(r=>el('option',{value:r.id,text:when(r.started_at)+' · '+(r.reader?.model||'Reader unavailable')+' · '+r.status})));selectedAssurance=runs.some(r=>r.id===previous)?previous:runs[0].id;select.value=selectedAssurance;
    const run=runs.find(r=>r.id===selectedAssurance),rows=run.rows||[];
    const graphs=rows.map(r=>el('div',{class:'quality-row'},[el('strong',{text:engineName(r.engine)}),el('div',{class:'quality-bars'},[bar('Security',r.components.security),bar('Privacy',r.components.privacy,'graph')]),el('span',{class:'bar-value',text:pct(r.components.security)+' / '+pct(r.components.privacy)})]));
    const body=[el('div',{class:'result-meta'},[el('strong',{text:run.name}),tag(run.status,run.status==='completed'?'good':'warning'),tag('Component scores · no full macro')]),el('p',{class:'field-help',text:(run.reader?.model||'Unknown model')+' @ '+(run.reader?.revision||'?')+' · '+run.completed+' / '+run.total+' registered pairs. Compare only matching model, packs, artifacts and profile.'}),chart('Safe useful completion','Security / Privacy · 0–100',graphs),table(['System','Security','Privacy','Pairs','Safe correct answers','Model tokens','Tokens / safe correct','Saved / safe correct','Critical gates'],rows.map(r=>[nameCell(r),pct(r.components.security),pct(r.components.privacy),r.completed_pairs+' / '+r.registered_pairs,String(r.safe_correct_answers),number(r.total_model_tokens,0),number(r.tokens_per_safe_correct_answer),number(r.tokens_saved_per_safe_correct_answer),r.critical_violations.join(', ')||'None observed'])),el('div',{class:'result-foot'},[el('span',{text:'Independent family-macro grading · bounded synthetic canary detection'}),button('Evidence & replay ↗',()=>details(run))])];
    for(const r of rows){body.push(el('details',{class:'coverage-details'},[el('summary',{text:engineName(r.engine)+' · utility, attack outcomes and case evidence'}),...Object.entries(r.packs||{}).map(([ref,p])=>el('div',{},[el('h3',{text:ref}),table(['Clean utility','Utility under attack','Attack success bounds','Unknown attacks','Families'],[[pct(p.clean_utility_percent),pct(p.utility_under_attack_percent),pct(p.attack_success_lower_percent)+' – '+pct(p.attack_success_upper_percent),String(p.unknown_attack_outcomes),String(Object.keys(p.families).length)]]),el('p',{class:'field-help',text:'Bounds include unobserved outcomes. These are four public templates, not a population risk estimate.'})])),...((run.cases||[]).filter(c=>c.engine===r.engine).map(c=>el('div',{class:'history-row'},[el('div',{},[el('strong',{text:c.case}),el('small',{text:c.family+' · '+(c.safe_useful?'Both tasks completed safely':'Task pair failed or incomplete')})]),button('Inspect pair',()=>showDialog(engineName(c.engine)+' · '+c.case,[el('div',{class:'boundary-flow'},['Source →','Captured context →','Fixed reader →','Mock gateway →','Offline grader'].map(text=>el('span',{text}))),el('p',{class:'field-help',text:'Protected values are redacted for display after grading raw evidence. Reference gateway decisions do not establish native engine authorization.'}),el('pre',{text:JSON.stringify(c,null,2)})]))])))]));}
    $('assurance-content').replaceChildren(el('article',{class:'panel'},body));
  }
  function history() {
    const runs=[...state.comparisons,...state.soaks,...(state.assurance||[]),...(state.studies||[])].sort((a,b)=>String(b.started_at).localeCompare(String(a.started_at)));
    $('history').replaceChildren(...(runs.length?runs.slice(0,12).map(run=>el('div',{class:'history-row'},[el('span',{class:'history-status '+(run.status==='completed'?'completed':run.status==='running'?'running':run.status==='failed'?'failed':'stopped'),'aria-hidden':'true'}),el('div',{},[el('span',{class:'history-title',text:run.name||run.id}),el('small',{text:when(run.started_at)+' · '+run.status+' · '+run.engines.map(engineName).join(', ')})]),button('Details →',()=>details(run))])):[el('p',{class:'field-help',text:'This workspace has no runs.'})]));
  }
  function catalog() {
    $('engine-catalog').replaceChildren(...state.engines.map(e=>el('article',{class:'panel engine-card',style:{'--engine-color':color(e.id)}},[el('div',{class:'engine-card-header'},[el('span',{class:'engine-emblem','aria-hidden':'true',text:'⬡'}),el('div',{},[el('h2',{text:e.name}),el('span',{class:'engine-category',text:e.category})]),tag(e.available?'Ready':'Setup required',e.available?'good':'')]),el('p',{text:e.description}),el('div',{class:'composition',text:e.composition}),el('div',{class:'engine-card-footer'},[el('span',{class:'mono',text:e.version?'v'+e.version:'Not bound'}),button('Setup instructions',()=>setupEngine(e))])])));
    $('capability-matrix').replaceChildren(table(['Integration','Adapter and runtime'],state.engines.map(e=>[e.name,e.composition])));
    const chosen=[...document.querySelectorAll('input[name=engine]:checked')].map(x=>x.value);
    $('engine-options').replaceChildren(...state.engines.map(e=>el('label',{class:'engine-option '+(e.available?'':'unavailable')},[el('input',{type:'checkbox',name:'engine',value:e.id,disabled:!e.available,...((initialized?chosen.includes(e.id):e.available)?{checked:'checked'}:{})}),el('span',{},[el('strong',{text:e.name}),el('small',{text:e.available?e.version:e.reason||'Setup required'})]),tag(e.available?'Ready':'Unavailable',e.available?'good':'')])));
    const oldBaseline=$('baseline').value;
    $('baseline').replaceChildren(...state.engines.filter(e=>e.available).map(e=>el('option',{value:e.id,text:e.name})));
    $('baseline').value=state.engines.some(e=>e.id===oldBaseline&&e.available)?oldBaseline:state.engines.some(e=>e.id==='networkx'&&e.available)?'networkx':state.engines.find(e=>e.available)?.id||'';
    const oldReader=$('reader').value;
    $('reader').replaceChildren(el('option',{value:'',text:'No reader · context metrics only'}),...(state.readers||[]).map(r=>el('option',{value:r.path,text:r.model+' @ '+r.revision+' ('+r.backend+')'})));
    if((state.readers||[]).some(r=>r.path===oldReader))$('reader').value=oldReader;
    const packs=state.test_packs?.packs||[];
    $('pack-options').replaceChildren(...packs.map(p=>el('label',{class:'engine-option'},[el('input',{type:'checkbox',name:'pack',value:p.reference,...(p.core_development?{checked:'checked'}:{})}),el('span',{},[el('strong',{text:p.title}),el('small',{text:p.reference+' · '+p.case_count+' cases · '+(p.core_development?'Core development':'Experimental')})])])));
    $('pack-catalog').replaceChildren(table(['Pack','Component','Cases / families','Driver / grader','License / affiliation'],packs.map(p=>[p.reference+(p.core_development?' · Core development':' · Experimental'),p.component,p.case_count+' / '+p.families.length,p.driver+' / '+p.grader,p.license+' · '+p.authors.map(a=>a.affiliation).join('; ')])));
    initialized=true;
  }
  function setupEngine(e) {
    const instructions={cigar:'cgb configure-cigar --sdk-root /absolute/path/to/sdk-parent \\\n  --worker /absolute/path/to/cigar-worker \\\n  --output .cgb/engines/cigar.json',
      'lean-ctx':'cgb platform bind-lean-ctx --executable /absolute/path/to/lean-ctx \\\n  --output .cgb/engines/lean-ctx.json',networkx:"python -m pip install -e '.[graphs]'"};
    showDialog(e.name+' setup',[el('p',{text:e.description}),el('div',{class:'composition',text:e.composition}),el('div',{class:'code-box'},[el('code',{text:instructions[e.id]||'cgb platform register --root . --id '+e.id+' --descriptor path/to/adapter.json'})]),el('p',{class:'field-help',text:'Run setup commands in the project terminal. Bind the exact installed runtime before a scored run. '+e.setup}),el('p',{class:'field-help',text:e.reason||'This integration is available.'})]);
  }
  const steps=[['Scenario','Freeze the task, not the outcome.','Public source documents, relationships, scoped tasks and budgets enter the adapter. Correctness labels stay with the evaluator. The included synthetic tasks are development data, not hidden test certification.'],['Adapter','Measure the whole named composition.','An engine receives the same task contract. Native behavior and trusted reference code are disclosed. Index construction, filtering and packing are real work and remain inside the measured timing boundary.'],['Evidence','Verify what was actually delivered.','The engine returns source text and citations. The reference renderer counts complete context including citation framing. The scorer checks authorization, versions, source spans and budget independently.'],['Outcomes','A correct answer has a measurable cost.','Optional fixed AI readers answer from captured context. Independent typed targets grade full answers and abstentions. All model attempts and known token costs remain in the denominator; unsupported or missing usage stays unavailable.'],['Replay','Keep the evidence inspectable.','The harness seals original attempts, configurations, code identity and results. Data-only replay re-derives metrics without running submitted engine code. External reproduction and diverse prospective tasks are still required for rankings.']];
  function methodology() {
    $('pipeline').replaceChildren(...steps.map((s,i)=>buttonStep(s,i)));
    selectStep(0);
    buildGraph('complete');
    $('graph-controls').replaceChildren(...[['complete','Complete context'],['missing','Missing dependency'],['scope','Scope changes']].map(([id,label])=>el('button',{type:'button','aria-pressed':id==='complete',text:label,onClick:()=>{for(const b of $('graph-controls').children)b.setAttribute('aria-pressed',b.textContent===label?'true':'false');buildGraph(id);}})));
    const dimensions=state.policy.dimensions;
    $('score-policy-heading').textContent='Weighted score calculator';
    $('token-weight').textContent=pct(100*dimensions.find(d=>d.id==='tokens').weight);
    $('reliability-weight').textContent='Reliability '+pct(100*dimensions.find(d=>d.id==='reliability').weight);
    let cumulative=0;const stops=dimensions.map((d,i)=>{const start=cumulative;cumulative+=d.weight*100;return componentColor(i)+' '+start+'% '+cumulative+'%';});
    $('weight-ring').style.background='conic-gradient('+stops.join(',')+')';
    $('weights').replaceChildren(...dimensions.map((d,i)=>el('div',{class:'weight-item'},[el('span',{class:'engine-dot',style:{background:componentColor(i)}}),el('span',{text:d.label}),el('strong',{text:number(d.weight*100)+'%'})])));
    const initial={retrieval:80,graph:75,answers:80,workflow:90,reliability:95,tokens:70,runtime:80,security:85,privacy:85};
    $('score-sliders').replaceChildren(...dimensions.map(d=>{const value=initial[d.id]??80,input=el('input',{id:'score-'+d.id,type:'range',min:0,max:100,value,'aria-label':d.label});const output=el('output',{for:input.id,text:value});input.addEventListener('input',()=>{output.textContent=input.value;updateDemo();});return el('label',{class:'slider-row',for:input.id},[el('span',{text:d.label}),input,output]);}));
    $('score-equation').textContent=state.policy.formula;
    $('assurance-requirements').replaceChildren(...Object.entries(state.policy.measurement_requirements||{}).map(([id,description])=>el('article',{class:'assurance-card'},[el('div',{class:'result-meta'},[el('h3',{text:dimensions.find(d=>d.id===id)?.label||id}),tag('Executable pack')]),el('p',{text:description})])));
    $('pipeline').dataset.policy=JSON.stringify(state.policy);
    updateDemo();
  }
  function buttonStep(s,i){return el('button',{type:'button','aria-pressed':i===0,onClick:()=>selectStep(i)},[el('span',{text:String(i+1)}),s[0]]);}
  function selectStep(i){[...$('pipeline').children].forEach((b,n)=>b.setAttribute('aria-pressed',n===i?'true':'false'));$('pipeline-detail').replaceChildren(el('h3',{text:steps[i][1]}),el('p',{text:steps[i][2]}));}
  function updateDemo() {
    const blocked = $('demo-violation').checked;
    const missing = $('demo-missing').checked;
    const dimensions = state.policy.dimensions;
    const total = dimensions.reduce((sum, d) => sum + d.weight * Number($('score-' + d.id).value), 0);
    $('demo-score').textContent = number(blocked || missing ? null : total, 2);
    $('demo-status').textContent = blocked ? 'Blocked by a mandatory check.' :
      missing ? 'Incomplete until every dimension is measured.' : 'Weighted sum of the nine slider values.';
  }
  function svg(tagName,attrs={},children=[]){const n=document.createElementNS('http://www.w3.org/2000/svg',tagName);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,String(v));for(const c of children)n.append(c);return n;}
  function buildGraph(mode){const root=svg('svg',{viewBox:'0 0 460 220',role:'img','aria-label':'Deployment depends on current artifact, rollback and policy; '+mode+' scenario'});const nodes=[{x:48,y:100,w:110,label:'Deployment',id:'deploy'},{x:276,y:25,w:116,label:'Artifact',id:'artifact'},{x:276,y:100,w:116,label:'Rollback',id:'rollback'},{x:276,y:175,w:116,label:'Policy',id:'policy'}];
    for(const node of nodes.slice(1))root.append(svg('path',{d:'M158 120 C218 120 220 '+(node.y+20)+' 276 '+(node.y+20),class:'edge',opacity:mode==='missing'&&node.id==='artifact'?.25:1}));
    for(const node of nodes){const denied=(mode==='missing'&&node.id==='artifact')||(mode==='scope'&&node.id==='policy');root.append(svg('rect',{x:node.x,y:node.y,width:node.w,height:40,rx:9,class:'node'+(denied?' denied':''),opacity:denied?.65:1}));const text=svg('text',{x:node.x+node.w/2,y:node.y+25,'text-anchor':'middle',fill:denied?'#f4b6c3':'#c5d7fa','font-size':11});text.textContent=node.label+(denied?' ×':'');root.append(text);}
    $('context-graph').replaceChildren(root);$('graph-explanation').textContent={complete:'Complete: required evidence travels with the deployment, within the same token budget.',missing:'Missing artifact: returning a confident deployment answer fails completeness. A required unavailable source should be refused.',scope:'Scope changes: policy outside the allowed view cannot be reused. Reference filtering is distinct from native engine authorization.'}[mode];}
  function route(focus=false){const current=location.hash.slice(1);const view=['results','studio','engines','methodology'].includes(current)?current:'results';for(const n of document.querySelectorAll('.view'))n.hidden=n.id!=='view-'+view;for(const a of document.querySelectorAll('[data-view]')){if(a.dataset.view===view)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');}if(focus){$(view+'-heading').focus({preventScroll:true});window.scrollTo(0,0);}}
  function activeRun(){const active=state.controller?.active_job,root=$('active-run');root.hidden=!active;if(!active)return;const run=[...state.comparisons,...state.soaks,...(state.assurance||[]),...(state.studies||[])].find(r=>r.id===active.id);let value=run?.total?100*run.completed/run.total:run?.seconds_per_engine?100*run.engine_elapsed_seconds/run.seconds_per_engine:0;root.replaceChildren(el('span',{class:'active-pulse'}),el('div',{},[el('strong',{text:active.name}),el('small',{text:run?.active_case?engineName(run.active_engine)+' · '+run.active_case+' · '+run.completed+' / '+run.total+' pairs':run?.active_engine?engineName(run.active_engine)+' · '+time(run.engine_elapsed_seconds)+' / '+time(run.seconds_per_engine):run?.total?run.completed+' / '+run.total+' attempts':run?.phase||active.summary}),el('progress',{value,max:100,'aria-label':'Active run progress'})]),button('Stop',async()=>{try{await control('/api/jobs/stop',{id:active.id});await refresh();}catch(e){showError(e);}}));}
  function showError(error){$('global-error').hidden=false;$('global-error').textContent=String(error.message||error);}
  async function control(path,payload){const token=document.querySelector('meta[name=cgb-control-token]')?.content;if(!token||snapshot)throw Error('Run controls require the live local dashboard.');const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CGB-Control-Token':token},body:JSON.stringify(payload)});const value=await r.json();if(!r.ok)throw Error(value.error||'Run request failed');return value;}
  function launchState(){const unavailable=snapshot||!state?.controller||busy||state.controller.active_job;$('launch').disabled=Boolean(unavailable);if(snapshot)$('launch-message').textContent='Read-only exported snapshot. Run the live local dashboard for execution controls.';}
  async function launch(event){event.preventDefault();if(busy)return;const selected=[...document.querySelectorAll('input[name=engine]:checked')].map(x=>x.value);if(!selected.length){$('launch-message').textContent='Select at least one available engine.';return;}const track=document.querySelector('input[name=track]:checked').value;const value={kind:{comparison:'platform-compare',soak:'platform-soak',assurance:'platform-assurance',study:'platform-study'}[track],engines:selected,name:$('run-name').value.trim()||'Context graph benchmark'};
    if(track==='comparison'){value.profiles=$('profiles').value==='both'?['evidence','graph']:[$('profiles').value];value.budgets=$('budgets').value.split(',').map(Number);value.replicates=Number($('replicates').value);}
    else{if($('reader').value)value.reader=$('reader').value;value.baseline=selected.includes($('baseline').value)?$('baseline').value:selected[0];value.isolation=$('isolation').value;if(track==='soak'||track==='study'){value.hours=$('duration').value==='24'?24:1;if($('duration').value==='pilot')value.pilot_seconds=15;if(track==='soak')value.assurance=$('assurance-overlay').checked;}else value.packs=[...document.querySelectorAll('input[name=pack]:checked')].map(x=>x.value);
      if(!value.reader&&(track==='assurance'||track==='study'||value.assurance)){$('launch-message').textContent='Select a bound local AI reader for these execution packs.';return;}}
    busy=true;launchState();$('launch-message').textContent='Registering run…';try{const job=await control('/api/jobs/start',value);pendingRun=job.id;selectedAssurance=null;$('launch-message').textContent='Started '+job.id+'. Original attempts will be retained.';location.hash='results';await refresh();}catch(error){$('launch-message').textContent=String(error.message||error);}finally{busy=false;launchState();}}
  async function refresh(){if(refreshInFlight)return;refreshInFlight=true;try{if(snapshot)state=snapshot;else{const r=await fetch('/api/platform',{cache:'no-store'});if(!r.ok)throw Error('Dashboard state unavailable ('+r.status+').');state=await r.json();}if(state.schema!=='cgb.platform.v1')throw Error('Unknown platform state.');$('connection').textContent=snapshot?'Offline snapshot':'Live · local';$('connection').classList.toggle('live',!snapshot);$('global-error').hidden=true;kpis();policyOverview();overallStudies();comparisons();assuranceResults();soaks();history();const catalogKey=JSON.stringify([state.engines,state.readers,state.test_packs]);if($('engine-catalog').dataset.key!==catalogKey){catalog();$('engine-catalog').dataset.key=catalogKey;}if($('pipeline').dataset.policy!==JSON.stringify(state.policy))methodology();activeRun();launchState();route();}catch(error){$('connection').textContent='Connection unavailable';$('connection').classList.remove('live');showError(error);}finally{refreshInFlight=false;}}
  function init() {
    window.addEventListener('hashchange',()=>route(true));
    $('refresh').addEventListener('click',refresh);
    $('comparison-select').addEventListener('change',()=>{pendingRun=null;selectedRun=$('comparison-select').value;comparisons();});
    $('overall-select').addEventListener('change',()=>{selectedOverall=$('overall-select').value;overallStudies();});
    $('assurance-select').addEventListener('change',()=>{selectedAssurance=$('assurance-select').value;assuranceResults();});
    for(const id of ['demo-missing','demo-violation'])$(id).addEventListener('change',updateDemo);
    $('close-dialog').addEventListener('click',()=>$('detail-dialog').close());
    $('detail-dialog').addEventListener('click',e=>{if(e.target===$('detail-dialog')&&e.clientX){const r=$('detail-dialog').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$('detail-dialog').close();}});
    $('run-form').addEventListener('submit',launch);
    for(const radio of document.querySelectorAll('input[name=track]'))radio.addEventListener('change',()=>{const track=document.querySelector('input[name=track]:checked').value;$('soak-settings').hidden=!['soak','study'].includes(track);$('comparison-settings').hidden=track!=='comparison';$('assurance-settings').hidden=track!=='assurance';$('reader-settings').hidden=track==='comparison';$('overlay-control').hidden=track!=='soak';});
    route();refresh();if(!snapshot)setInterval(refresh,4000);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
