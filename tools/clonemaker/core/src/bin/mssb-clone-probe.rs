//! File-oriented differential-test interface, separate from the user-facing CLI.
use mssb_clone_core::{Error, Result, binary::*, disc::*, lzss};
use std::{env, fs, path::Path};
fn run(a: &[String]) -> Result<()> {
    match a.first().map(String::as_str) {
        Some("codec") if a.len()==8 => {
            let data=fs::read(&a[2])?;
            let parse=|s: &str| s.parse::<usize>().map_err(|_| Error::new("invalid codec parameter".into()));
            let size=parse(&a[4])?;let lb=parse(&a[5])? as u32;let rp=parse(&a[6])? as u32;let chain=parse(&a[7])?;
            let out=match a[1].as_str() {
                "decode"=> {let (out,used)=lzss::decompress(&data,size,lb,rp)?;println!("{used}");out},
                "greedy"=>lzss::compress(&data,lb,rp,chain)?,
                "optimal"=>lzss::compress_optimal(&data,lb,rp,chain)?,
                _=>return Err(Error::new("unknown codec".into()))
            };fs::write(&a[3],out)?;
        }
        Some("api") if a.len()==3 => {
            use mssb_clone_core::api::*;
            let core=Core::default();let status=core.open_game(Path::new(&a[1]));
            let folder=Path::new(&a[2]);fs::create_dir_all(folder)?;
            let (game,_)=load_game(Path::new(&a[1]));let mut game=game.ok_or_else(||Error::new(status.message.clone()))?;
            let mut snapshot=serde_json::json!({"open":status,"sources":core.sources(),"output":default_output(Path::new(&a[1]))});
            let mut source_info=Vec::new();let mut choices=Vec::new();let mut hints=Vec::new();
            for source in 0..54 {
                source_info.push(core.source_info(source).map_err(|e|Error::new(e.error))?);
                choices.push(donor_choices(&game.dol,source)?);
                let rows=default_rows(choices.last().unwrap());let slots=rows.iter().map(|r|r.slot).collect::<Vec<_>>();hints.push(pick_hint(&game.dol,source,&slots,true)?);
                let pv=game.preview(source)?;
                fs::write(folder.join(format!("before-{source}.png")),&pv.before_png)?;
                let selected=pv.regions.iter().enumerate().filter(|(_,r)|r.default).map(|(i,_)|i).collect::<Vec<_>>();
                for (i,colour) in ["blue","green","#00ff00"].into_iter().enumerate() {
                    let request=PreviewRequest{source,slot:slots[0],colour:colour.into(),keeplum:0.5,regions:selected.clone()};let result=core.preview(&request).map_err(|e|Error::new(e.error))?;
                    fs::write(folder.join(format!("preview-{source}-{i}.json")),serde_json::to_vec(&result).unwrap())?;
                }
            }
            snapshot["source_info"]=serde_json::to_value(source_info).unwrap();snapshot["choices"]=serde_json::to_value(choices).unwrap();snapshot["hints"]=serde_json::to_value(hints).unwrap();
            fs::write(folder.join("snapshot.json"),serde_json::to_vec(&snapshot).unwrap())?;
        }
        Some("validate") if a.len()==3 => {
            use mssb_clone_core::api::*;
            let job:Job=serde_json::from_slice(&fs::read(&a[1])?).map_err(|e|Error::new(e.to_string()))?;
            let free=a[2].parse().map_err(|_|Error::new("invalid free-space count".into()))?;
            let error=validate_job(&job,Some(free)).err().map(ApiError::from);
            println!("{}",serde_json::to_string(&error).unwrap());
        }
        Some("job") if a.len()==2 || a.len()==3 => {
            use mssb_clone_core::api::*;
            use std::sync::atomic::AtomicBool;
            let job:Job=serde_json::from_slice(&fs::read(&a[1])?).map_err(|e|Error::new(e.to_string()))?;
            let cancelled=AtomicBool::new(a.len()==3);
            let result=run_job(&job,&|e|println!("{}",serde_json::to_string(&e).unwrap()),&cancelled)?;
            println!("{}",serde_json::to_string(&Event::Done(result)).unwrap());
        }
        Some("textures") if a.len()==4 => {
            use mssb_clone_core::{texture::*,colour::*,constants::*};
            let disc=Disc::folder(&a[1])?;let dol=Dol::new(disc.system("main.dol")?)?;
            let c:usize=a[2].parse().map_err(|_|Error::new("invalid char id".into()))?;
            let folder=Path::new(&a[3]);fs::create_dir_all(folder)?;
            let g=Gpc::new(disc.effective_body(&dol,c)?)?;g.check()?;
            let regions=source_regions(&disc,&dol,c,&g)?;
            fs::write(folder.join("regions.json"),serde_json::to_vec(&regions).unwrap())?;
            for t in &g.textures {fs::write(folder.join(format!("decode-{}.rgb",t.index)),decode_texture(&t.bytes(&g.data)?,t)?)?;}
            for preset in PRESETS.iter().map(|p|p.0).chain(["hue=0.317,sat=1.1,keeplum=0.73","tint=00ff99:0.3","all=1,hue=yellow","hue=blue,region=0"]) {
                let mut rc=Recolour::new(preset)?;rc.resolve(&regions)?;let mut out=g.clone();rc.recolour_gpc(&mut out)?;
                fs::write(folder.join(format!("recolour-{preset}.gpc")),out.data)?;
                let pal=rc.for_palette();let values:Vec<u8>=(0..=65535).flat_map(|v|pal.map_rgb5a3(v).to_be_bytes()).collect();
                fs::write(folder.join(format!("palette-{preset}.bin")),values)?;
            }
        }
        Some("write-iso") if a.len()==3 => write_iso(&Disc::folder(&a[1])?,Path::new(&a[2]),&mut |s| println!("{s}"),None)?,
        Some("extract-iso") if a.len()==3 => extract_iso(Path::new(&a[1]),Path::new(&a[2]),&mut |s| println!("{s}"),None)?,
        Some("inspect") if a.len()==2 => {
            let d=Disc::folder(&a[1])?;let dol=Dol::new(d.system("main.dol")?)?;
            preflight(&d,&dol,&mut |s| println!("{s}"))?;
            for s in &dol.sections {println!("section {} {} {}",s.offset,s.address,s.size);}
            println!("dol_size {}",dol_size(&dol.data)?);
            for (e,p) in fst_paths(&d.system("fst.bin")?)? {println!("fst {} {} {} {}",e.index,e.offset,e.size,p);}
            for c in 0..54 {let (b,o)=dol.pair(c)?;println!("char {c} {b} {o} {:?} {:?}",dol.private_entry(c)?,dol.family(c)?);}
            for i in 0..3 {let rel=d.load_rel(&dol,i)?;let hdr=rel_header(&rel)?;println!("rel {i} {} {:?} {:?}",hdr.module,hdr.sections,hdr.imports);for r in rel_self_relocs(&rel)? {println!("reloc {} {:?} {} {} {} {}",r.entry,r.section,r.offset,r.kind,r.target_section,r.addend);}}
        }
        _=>return Err(Error::new("usage: mssb-clone-probe codec KIND IN OUT SIZE LB RP CHAIN | write-iso IN OUT | extract-iso IN OUT | inspect FOLDER".into()))
    }
    Ok(())
}
fn main() {
    if let Err(e) = run(&env::args().skip(1).collect::<Vec<_>>()) {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
