"use strict";
const $ = id => document.getElementById(id);
const node = (tag, text, cls) => {
  const e = document.createElement(tag);
  if (text != null) e.textContent = text;
  if (cls) e.className = cls;
  return e;
};
const labels = {ack:"标记已阅", retain:"接受完整保留原块", split:"断开为两句", join:"合为一句",
  apply:"函数应用",multiply:"乘积",label:"标签",index:"指标",constraint:"约束",proposition:"命题",
  predicate:"谓词",predicate_part:"谓词成分",condition:"条件",guard:"分支条件",consequent:"后件",
  scope:"量化范围",connective:"连接词",restriction:"限制",default:"默认分支",text:"一般文本",operator:"运算符",
  allow:"保留命令",font:"字体命令",unwrap:"保留参数",ignore:"忽略命令",expand:"展开定义"};
let data = null, section = "", kind = "", selected = "", page = 0, busy = false, running = false, timer = null;
const expanded = new Set(["blank"]);
const sectionOf = i => i.required ? "blank" : "labeled";
const pageSize = 10;
const identity = i => JSON.stringify([i.kind, i.target]);
const state = i => i.decision ? (i.decision.source || "manual") : i.level;
const stateLabel = {manual:"人工确认 · manual",model:"模型确认 · model",todo:"必办",optional:"可选检查",warning:"警告",error:"错误"};
const badge = i => node("span", i.decision?.answer==="ack" ? "已阅" : stateLabel[state(i)], "tag " + state(i));
function notice(msg, bad=false) { $("message").className=bad ? "error" : ""; $("message").textContent=msg; }
async function api(path, body) {
  const opt = body ? {method:"POST", headers:{"Content-Type":"application/json",
    "X-Review-Token":document.querySelector('meta[name="review-token"]').content}, body:JSON.stringify(body)} : {};
  const res = await fetch("/api/" + path, opt);
  const obj = await res.json();
  if (!res.ok) throw new Error(obj.error || "请求失败，请刷新。");
  return obj;
}
function matches(i) {
  const mode=$("filter").value;
  if (mode==="comparison" && (!i.comparison || i.comparison.status==="confirmed")) return false;
  if (mode==="warnings" && !["warning","error"].includes(i.level)) return false;
  if (mode==="todo" && !i.required) return false;
  if (mode==="optional" && i.required) return false;
  if (["manual","model"].includes(mode) && state(i)!==mode) return false;
  const q=$("search").value.trim().toLowerCase();
  return !q || [data.groups[i.kind],i.title,i.context,i.location,i.current,i.suggestion,i.label_source,i.decision?.note,JSON.stringify(i.detail)].join(" ").toLowerCase().includes(q);
}
function render(prefer=false) {
  if (!data) return;
  $("paperName").textContent=data.name; $("paperName").title=data.directory;
  const visible = data.items.filter(matches).sort((a,b)=>Number(b.required)-Number(a.required));
  const sections = data.sections || data.subgroups;
  const nav=$("groups");
  nav.replaceChildren();
  const counts = new Map(Object.keys(sections).map(key=>[key,new Map()]));
  for (const item of visible) {
    const byKind = counts.get(sectionOf(item));
    byKind.set(item.kind, (byKind.get(item.kind)||0)+1);
  }
  function categoryButton(key,category,label,count) {
    const active = key===section && category===kind;
    const button=node("button",label,active?"active":"");
    button.dataset.section=key;
    button.dataset.kind=category;
    if (active) button.setAttribute("aria-current","true");
    button.append(node("span",count));
    button.onclick=()=>{
      section=key; kind=category; page=0; selected="";
      if (["todo","optional"].includes($("filter").value)) $("filter").value="all";
      expanded.add(key);
      render();
      nav.querySelector('button[aria-current="true"]').focus();
    };
    return button;
  }
  for (const [key,label] of Object.entries(sections)) {
    const byKind=counts.get(key);
    const total=Array.from(byKind.values()).reduce((sum,count)=>sum+count,0);
    const branch=node("div",null,"navsection");
    const head=node("div",null,"navparent");
    const open=expanded.has(key);
    const toggle=node("button",open?"▾":"▸","disclosure");
    const childId="categories-"+key;
    toggle.setAttribute("aria-label",(open?"折叠":"展开")+label);
    toggle.setAttribute("aria-expanded",String(open));
    toggle.setAttribute("aria-controls",childId);
    toggle.onclick=()=>{
      if (open) expanded.delete(key); else expanded.add(key);
      render();
      nav.querySelector('[aria-controls="'+childId+'"]').focus();
    };
    const parent=categoryButton(key,"",label,total);
    const caption=node("strong",label);
    caption.append(node("small",key==="blank"?"必办":"可选检查"));
    parent.replaceChild(caption,parent.firstChild);
    head.append(parent,toggle);
    const children=node("div",null,"navchildren");
    children.id=childId;
    children.hidden=!open;
    children.setAttribute("role","group");
    children.setAttribute("aria-label",label);
    for (const [category,title] of Object.entries(data.groups)) {
      children.append(categoryButton(key,category,title,byKind.get(category)||0));
    }
    branch.append(head,children);
    nav.append(branch);
  }
  $("allItems").disabled=!section;
  const list=visible.filter(i=>(!section || sectionOf(i)===section) && (!kind || i.kind===kind));
  const ix=list.findIndex(i=>identity(i)===selected);
  if (prefer && ix>=0) page=Math.floor(ix/pageSize);
  page=Math.min(page,Math.max(0,Math.ceil(list.length/pageSize)-1));
  const batch=list.slice(page*pageSize,(page+1)*pageSize);
  const active=batch.find(i=>identity(i)===selected)||batch[0];
  selected=active ? identity(active):"";
  $("title").textContent=kind ? sections[section]+" / "+data.groups[kind] : sections[section] || "审核清单";
  $("count").textContent=list.length+" 项";
  $("pages").textContent=(list.length?page+1:0)+" / "+Math.ceil(list.length/pageSize);
  $("prev").disabled=!page; $("next").disabled=(page+1)*pageSize>=list.length;
  const box=$("list"); box.replaceChildren();
  for (const i of batch) {
    const b=node("button",null,"row"+(identity(i)===selected?" active":""));
    const head=node("span",null,"rowhead"); head.append(node("span",i.location||data.groups[i.kind],"loc"),badge(i));
    b.append(head,node("strong",i.title),node("small",i.context||data.groups[i.kind]));
    b.onclick=()=>{selected=identity(i);render();}; box.append(b);
  }
  if (!batch.length) box.append(node("p","当前筛选下没有条目。","empty"));
  editing=false;
  detail(active);
  modelControls();
}
function field(caption,el) { const l=node("label",caption); l.append(el); return l; }
function option(value) { const o=node("option",labels[value] ? labels[value]+" · "+value : value); o.value=value; return o; }
function structureEditor(i, box) {
  const isRef=i.kind==="destination", cards=node("div"), readers=[];
  const initial=i.decision?.value ?? i.detail.proposal;
  function add(value={}) {
    const card=node("fieldset"), fields={};
    const names=isRef ? [["label","引用键"],["formula","目标公式 ID"],["row","目标行（整式留空）"]] :
      [["rows","原公式行号（逗号分隔）"],["lhs","左侧"],["symbol","关系符"],["rhs","右侧"],["expression","独立表达式（无关系时填写）"]];
    for (const [key,title] of names) {
      const input=node("input");
      input.value=Array.isArray(value[key]) ? value[key].join(",") : value[key] ?? "";
      fields[key]=input;card.append(field(title,input));
    }
    const remove=node("button","移除此项");
    const read=()=>{
      if (isRef) {
        const row={label:fields.label.value,formula:fields.formula.value.trim()};
        if (fields.row.value.trim()) row.row=Number(fields.row.value);
        return row;
      }
      const row=fields.expression.value.trim() ? {expression:fields.expression.value} :
        {lhs:fields.lhs.value,symbol:fields.symbol.value,rhs:fields.rhs.value};
      if (fields.rows.value.trim()) row.rows=fields.rows.value.split(/[,，]/).map(n=>Number(n.trim()));
      return row;
    };
    readers.push(read);
    remove.onclick=()=>{readers.splice(readers.indexOf(read),1);card.remove();};
    card.append(remove);cards.append(card);
  }
  box.append(node("h3",i.decision ? (state(i)==="model"?"模型读法":"人工读法") : "程序参考值"),cards);
  for (const value of initial) add(value);
  const plus=node("button",isRef?"添加引用目标":"添加关系或独立表达式");
  plus.onclick=()=>add(isRef?{label:i.detail.labels[0]}:{});
  box.append(plus);
  if (!isRef) box.append(node("p","确认空列表表示此处没有可提取的关系。","hint"));
  return ()=>readers.map(read=>read());
}
function detail(i) {
  const box=$("detail");box.replaceChildren();
  if (!i) {box.append(node("p","没有待显示的审核详情。","empty"));return;}
  const head=node("div",null,"rowhead");head.append(node("span",data.groups[i.kind]+"  /  "+(i.location||"整篇"),"loc"),badge(i));
  box.append(head,node("h2",i.title));
  if (["construction","slot","role"].includes(i.kind) || (i.decision && !["ack","review"].includes(i.decision.answer)) || i.comparison) {
    const hints=i.suggestions?.length ? i.suggestions.join(" / ") : i.suggestion;
    const value=i.decision?.answer || i.current;
    const source=i.decision ? state(i) : i.label_source;
    const info=value ? "当前标签　"+value+(source?" · "+source:"") :
      hints ? "默认建议　"+hints+"（尚未确认）" :
      i.conflict ? "已有答案互斥，请选择此处的读法。" : "当前标签空白，尚无默认建议。";
    box.append(node("p",info,"labelinfo"));
    if (i.conflict && !i.decision) box.append(node("pre",JSON.stringify(i.conflict,null,2)));
  }
  if (state(i)==="model") box.append(node("p","审核模型　"+i.decision.model,"labelinfo"));
  if (i.context) box.append(node("h3","原文与上下文"),node("p",i.context,"context"));
  if (i.detail.latex) box.append(node("h3","公式"),node("pre",i.detail.latex));
  if (i.comparison) box.append(node("p","规则："+i.comparison.rule_answer+"　模型："+i.comparison.model_answer+"　（"+i.comparison.model+"）",i.comparison.status==="confirmed"?"labelinfo":"compare"));
  const extra={...i.detail};delete extra.latex;
  if (Object.keys(extra).length) {
    const more=node("details",null,"evidence");more.append(node("summary","定位与原始记录"),node("pre",JSON.stringify(extra,null,2)));box.append(more);
  }
  const structured=["relation","destination"].includes(i.kind);
  const readValue=structured?structureEditor(i,box):null;
  const choice=node("select");choice.id="answer";
  if (i.options.length>1) {const empty=node("option","请选择");empty.value="";choice.append(empty);}
  for (const v of i.options) choice.append(option(v));
  choice.value=i.decision?.answer || (i.options.includes(i.current)?i.current:"") || (i.options.includes(i.suggestion) && !(i.suggestions?.length>1)?i.suggestion:"") || (i.options.length===1?i.options[0]:"");
  if (!structured) box.append(field("人工决定",choice));
  const cls=node("select");for(const v of data.classes) if(v!=="font" && v) cls.append(option(v));
  cls.value=i.decision?.class||"const";
  const body=node("textarea");body.value=i.decision?.body||"";
  const classField=field("命令类别",cls),bodyField=field("展开体",body);
  box.append(classField,bodyField);
  function nameFields(){classField.hidden=i.kind!=="names"||choice.value!=="allow";bodyField.hidden=i.kind!=="names"||choice.value!=="expand";}
  choice.onchange=nameFields;nameFields();
  const note=node("textarea"); note.id="note";note.maxLength=4000;note.value=i.decision?.note||"";
  box.append(field("审核备注（选填）",note));
  if(i.options[0]==="ack") box.append(node("p","已阅会保存备注。源码缺失、条件未定或硬错误仍需修复后复验。","hint"));
  const buttons=node("div",null,"buttons"), save=node("button",state(i)==="model"?"保存为人工确认":i.decision?"保存修改":"确认","save");
  save.disabled=running||busy;save.onclick=()=>submit(i,"save",{value:readValue?.(),answer:choice.value,class:cls.value,body:body.value,note:note.value}); buttons.append(save);
  if(i.decision){const undo=node("button","撤销确认");undo.disabled=running||busy;undo.onclick=()=>submit(i,"undo",{});buttons.append(undo);}
  box.oninput=()=>{editing=true;};
  box.append(buttons,node("div","来源文件　"+i.files.join(" · "),"evidence"));
}
let modelJob = {}, editing = false;
function modelControls(){
  const active=data?.items.find(i=>identity(i)===selected);
  const scope=$("modelScope").value;
  const eligible=(data?.items||[]).filter(i=>i.model_eligible && (scope==="all" || (scope==="required"?i.required:i===active)));
  const count=eligible.length;
  $("modelName").textContent=data?.model?.name || data?.model?.error || "";
  $("modelStart").textContent=scope==="item"?"判断本条":"判断 "+count+" 项";
  $("modelStart").disabled=!data?.model?.name||!count||busy||running||!!modelJob.running;
  $("modelScope").disabled=!!modelJob.running;
  $("modelStop").hidden=!modelJob.running;
  $("recheck").disabled=running||busy||!!modelJob.running;
  $("refresh").disabled=running||busy;
}
function modelProgress(job){
  modelJob=job || {};
  if(job?.total){
    const status=job.running?"运行中":job.stopped?"已停止":job.processed<job.total?"已中断":"已完成";
    $("modelProgress").textContent=status+" · "+job.model+"　"+job.processed+"/"+job.total+
      "　已保存 "+job.saved+"（规则分歧 "+job.disagreements+"）　弃权 "+job.abstained+"　跳过 "+job.skipped+"　失败 "+job.failed;
    const d=$("modelDetails"); d.hidden=!job.details.length;
    d.querySelector("ul").replaceChildren(...job.details.map(x=>node("li",((data?.groups||{})[x.kind]||x.kind)+" "+x.location+"　"+x.message)));
  }
  modelControls();
}
async function reload(){data=await api("items");editing=false;render(true);}
async function submit(i,op,fields){
  if(busy||running)return;
  busy=true;
  $("recheck").disabled=$("refresh").disabled=true;
  for (const button of $("detail").querySelectorAll(".buttons button")) button.disabled=true;
  try {
    await api("decision",{paper:data.paper,kind:i.kind,target:i.target,version:i.version,op,...fields});
    notice(op==="undo"?"已撤销。应用并复验后恢复此前的自动结果或待办。":"已保存人工确认。应用并复验后更新切分产物。");
    await reload();
  } catch(e){notice(e.message,true);}
  finally{busy=false; modelControls();
    for(const b of $("detail").querySelectorAll(".buttons button"))b.disabled=running;}
}
async function poll(){
  try{
    clearTimeout(timer);
    const job=await api("status");running=job.running;
    const wasModel=modelJob.running;
    modelProgress(job.model_job);
    if(running){notice("正在离线应用审核决定并复验…");render();timer=setTimeout(poll,900);return;}
    if(modelJob.running){if(!data)await reload();timer=setTimeout(poll,900);return;}
    if(wasModel && editing){notice("模型审核结束。可保存正在编辑的条目，或点击刷新查看最新决定。");return;}
    await reload();
    if(wasModel){notice("模型审核结束。点击“应用并复验”更新切分产物。");return;}
    if(job.result!=null) notice(job.result===0?"复验通过。":job.result===3?"复验完成，仍有待处理项。":"复验失败（"+job.result+"） "+job.message,![0,3].includes(job.result));
  }catch(e){notice(e.message,true);}
}
$("modelScope").onchange=modelControls;
$("modelStart").onclick=async()=>{
  if($("modelStart").disabled)return;
  const i=data.items.find(x=>identity(x)===selected), scope=$("modelScope").value;
  busy=true;modelControls();
  try{
    await api("model-review",{paper:data.paper,scope,...(scope==="item"?{kind:i.kind,target:i.target,version:i.version}:{})});
    await poll();
  }catch(e){notice(e.message,true);}finally{busy=false;modelControls();}
};
$("modelStop").onclick=async()=>{
  try{await api("model-stop",{});notice("已请求停止。当前尚未保存的回答将丢弃。");}catch(e){notice(e.message,true);}
};
$("prev").onclick=()=>{page--;selected="";render();};$("next").onclick=()=>{page++;selected="";render();};
$("allItems").onclick=()=>{
  section="";kind="";page=0;selected="";
  if (["todo","optional"].includes($("filter").value)) $("filter").value="all";
  render();
};
$("search").oninput=()=>{page=0;selected="";render();};
$("filter").oninput=()=>{
  const mode=$("filter").value;
  if (mode==="todo" || mode==="optional") {
    section=mode==="todo"?"blank":"labeled";kind="";expanded.add(section);
  }
  page=0;selected="";render();
};
$("refresh").onclick=()=>reload().then(()=>notice("已从运行目录重新读取。")).catch(e=>notice(e.message,true));
$("recheck").onclick=async()=>{if(!data||running||busy)return;try{await api("recheck",{paper:data.paper});await poll();}catch(e){notice(e.message,true);}};
poll();
