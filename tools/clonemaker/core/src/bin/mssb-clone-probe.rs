//! File-oriented differential-test interface, separate from the user-facing CLI.
use mssb_clone_core::{Error, Result, binary::*, disc::*, lzss};
use std::{env, fs, path::Path};
fn run(a: &[String]) -> Result<()> {
    match a.first().map(String::as_str) {
        Some("codec") if a.len()==8 => {
            let data=fs::read(&a[2])?;
            let parse=|s: &str| s.parse::<usize>().map_err(|_| Error("invalid codec parameter".into()));
            let size=parse(&a[4])?;let lb=parse(&a[5])? as u32;let rp=parse(&a[6])? as u32;let chain=parse(&a[7])?;
            let out=match a[1].as_str() {
                "decode"=> {let (out,used)=lzss::decompress(&data,size,lb,rp)?;println!("{used}");out},
                "greedy"=>lzss::compress(&data,lb,rp,chain)?,
                "optimal"=>lzss::compress_optimal(&data,lb,rp,chain)?,
                _=>return Err(Error("unknown codec".into()))
            };fs::write(&a[3],out)?;
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
        _=>return Err(Error("usage: mssb-clone-probe codec KIND IN OUT SIZE LB RP CHAIN | write-iso IN OUT | extract-iso IN OUT | inspect FOLDER".into()))
    }
    Ok(())
}
fn main() {
    if let Err(e) = run(&env::args().skip(1).collect::<Vec<_>>()) {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
