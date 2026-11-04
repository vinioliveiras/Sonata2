//! Locked folders (sonata2/vault.py, the part the lock screen needs): the
//! ones open now are closed when the screen locks -- opening them again
//! asks for their password.

pub const SUFFIX: &str = ".sonata-vault";

/// The locked folders open now (gocryptfs mounts of a "<name>.sonata-vault").
pub fn open_vaults(mounts: &str) -> Vec<String> {
    mounts
        .lines()
        .filter_map(|l| {
            let p: Vec<&str> = l.split_whitespace().collect();
            (p.len() >= 3 && p[2] == "fuse.gocryptfs" && p[0].ends_with(SUFFIX)).then(|| p[1].replace("\\040", " "))
        })
        .collect()
}

/// Every locked folder closed (unmounted). One in use stays open.
pub fn close_all() {
    let tool = ["fusermount3", "fusermount"].into_iter().find(|t| crate::run::which(t).is_some());
    let Some(tool) = tool else { return };
    for path in open_vaults(&std::fs::read_to_string("/proc/self/mounts").unwrap_or_default()) {
        crate::run::run(&[tool, "-u", &path], 30);
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn open_vaults_from_the_mount_table() {
        let mounts = "/home/v/.Taxes.sonata-vault /home/v/Taxes fuse.gocryptfs rw 0 0\n/dev/sda1 / ext4 rw 0 0\n/home/v/.My\\040Stuff.sonata-vault /home/v/My\\040Stuff fuse.gocryptfs rw 0 0\nother /x fuse.gocryptfs rw 0 0\n";
        assert_eq!(super::open_vaults(mounts), ["/home/v/Taxes", "/home/v/My Stuff"]);
    }
}
