import { sides, positions, captainSlots, singleTeam, unpackTeam, makeHUD, teamsFromHUD } from "./rio-roster.mjs";
const button = (text, action) => {
  const b = document.createElement("button"); b.type = "button"; b.textContent = text;
  b.addEventListener("click", () => run(action)); return b;
};
const node = (tag, text) => { const n = document.createElement(tag); n.textContent = text; return n; };
const form = document.getElementById("rio-teams");
const body = form.querySelector(".team-sheets");
const message = form.querySelector("[role=status]");
const imageInfo = form.querySelector(".team-image");
const invoke = window.__TAURI__.core.invoke;
let image, working = false, labels = [], lastFocus;
const initialTeams = () => Object.fromEntries(sides.map((side,t) => [side,{player:"",rows:positions.map((_,i) =>
  ({id:t === 0 ? i : i+9,pos:i,captain:i === 0,star:false}))}]));
const teams = initialTeams();
async function run(action) {
  if (working) return;
  working=true;
  form.querySelector("fieldset").disabled=true;
  message.textContent="";
  try { await action(); }
  catch (e) { message.textContent = typeof e === "string" ? e : e.message || String(e); }
  finally { working=false; form.querySelector("fieldset").disabled=false; }
}
function select(items, value, label, changed) {
  const s=document.createElement("select"); s.setAttribute("aria-label",label);
  for (const [v,name] of items) s.add(new Option(name,String(v)));
  s.value=String(value); s.onchange=()=>changed(Number(s.value)); return s;
}
function check(type, checked, label, changed) {
  const input=document.createElement("input"); input.type=type; input.checked=checked; input.setAttribute("aria-label",label);
  input.onchange=()=>changed(input.checked); return input;
}
async function readJson(file) {
  if (file.size > 1024*1024) throw Error("Choose a team or HUD JSON file under 1 MiB.");
  return JSON.parse(await file.text());
}
function importButton(text, callback) {
  const input=document.createElement("input"); input.type="file"; input.accept=".json,application/json"; input.hidden=true;
  const span=document.createElement("span"); const b=document.createElement("button"); b.type="button"; b.textContent=text;
  b.onclick=()=>input.click(); input.onchange=()=>run(async()=>{const f=input.files[0]; input.value=""; if(f) await callback(await readJson(f));});
  span.append(b,input); return span;
}
async function save(doc,single) {
  const path=await invoke("save_rio_roster",{json:JSON.stringify(doc,null,2)+"\n",single});
  if (path) message.textContent="Saved " + path + (single ? "" : " · Matchup " + await matchupId(doc));
}
async function matchupId(doc) {
  const data = new TextEncoder().encode(JSON.stringify(doc));
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest).slice(0,8),b=>b.toString(16).padStart(2,"0")).join("");
}
function draw() {
  body.replaceChildren(); if (!image) return;
  for (const side of sides) {
    const team=teams[side], section=node("section",""), heading=node("h3",side+" team");
    const name=document.createElement("input"); name.value=team.player; name.maxLength=30;
    const label=node("label","Rio lobby name ");label.append(name);name.oninput=()=>team.player=name.value;
    const table=document.createElement("table"), head=table.createTHead().insertRow();
    for(const col of ["Bat","Character slot","Field","Captain","Star"])head.append(node("th",col));
    const entries=table.createTBody();
    team.rows.forEach((r,i)=>{
      const row=entries.insertRow();row.insertCell().textContent=String(i+1);
      const key=side+" batter "+(i+1);
      const character=row.insertCell();
      const nickname=document.createElement("input");nickname.className="roster-label";nickname.maxLength=64;
      nickname.placeholder="Clone label (optional)";nickname.value=r.label || "";
      nickname.setAttribute("aria-label",key+" clone label");nickname.oninput=()=>r.label=nickname.value;
      character.append(select(labels,r.id,key+" character",v=>{
        r.id=v; delete r.label; nickname.value="";
        captain.disabled=!captainSlots.has(v); if(captain.disabled) { r.captain=false; captain.checked=false; }
      }),nickname);
      row.insertCell().append(select(positions.map((s,n)=>[n,s]),r.pos,key+" fielding position",v=>r.pos=v));
      const captain=check("radio",r.captain,key+" captain",()=>team.rows.forEach((x,j)=>x.captain=i===j));
      captain.title="Choose a roster member in a captain slot."; captain.disabled=!captainSlots.has(r.id);
      captain.name="team-captain-"+side; row.insertCell().append(captain);
      row.insertCell().append(check("checkbox",r.star,key+" superstar",v=>r.star=v));
    });
    section.append(heading,label,table,button("Save team…",()=>save(singleTeam(team,image.sha256),true)),
      importButton("Load team…",doc=>{teams[side]=unpackTeam(doc,image.sha256);draw();message.textContent=side+" team loaded.";}));
    body.append(section);
  }
}
export function showTeamImage(selected) {
  const changed = image && image.sha256 !== selected.sha256;
  if (changed) Object.assign(teams, initialTeams());
  image=selected;imageInfo.textContent=image.name+" · SHA-256 "+image.sha256;
  form.querySelector(".team-actions").hidden=false;draw();
  message.textContent=changed?"New game image selected. Choose or import its teams.":"Team slots and handedness will use this game image.";
}
const pick=button("Choose finished ISO…",async()=>{
  message.textContent="Reading the finished game and calculating its SHA-256…";
  const selected=await invoke("rio_team_image");
  if (selected) showTeamImage(selected);
  else message.textContent="";
});
form.querySelector(".team-pick").append(pick);
form.querySelector(".team-actions").append(
  importButton("Load both teams…",async doc=>{
    const loaded=teamsFromHUD(doc,image);
    Object.assign(teams,loaded.teams);draw();
    message.textContent=loaded.verified?"Game image verified · Matchup "+await matchupId(makeHUD(teams,image)):"Imported rosters from Rio. Check the character slots against your game before saving.";
  }),
  button("Save both for Rio…",()=>save(makeHUD(teams,image),false)),
  button("Use in Rio…",async()=>{
    const doc=makeHUD(teams,image);
    const result=await invoke("install_rio_matchup",{json:JSON.stringify(doc,null,2)+"\n"});
    if(result) message.textContent="Matchup "+await matchupId(doc)+" · "+result;
  })
);
form.querySelector(".team-close").onclick=()=>form.close();
body.addEventListener("input",()=>{message.textContent="";});
form.addEventListener("close",()=>lastFocus?.focus());
form.addEventListener("cancel",e=>{if(working)e.preventDefault();});
for (const trigger of document.querySelectorAll("[data-open-teams]")) {
  trigger.onclick=async()=>{
    if(form.open)return;
    lastFocus=trigger;
    const names=await invoke("sources");
    labels=names.map(s=>[s.id,s.name+" (#"+s.id+")"]);
    draw();form.showModal();
  };
}
