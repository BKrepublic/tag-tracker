// Run ONLY on Google's Find Hub encryption unlock or its PIN challenge page.
// Inspect before pasting; it never sends data or stores it outside this page.
(() => {
  const unlock = location.pathname === "/encryption/unlock/android";
  const pin = location.pathname === "/v3/signin/challenge/kls" &&
      new URLSearchParams(location.search).get("flowName") === "EncryptionUnlockAndroid";
  if (location.origin !== "https://accounts.google.com" || !(unlock || pin)) {
    throw new Error("Wrong page: open the Find Hub encryption unlock / PIN challenge page.");
  }
  window.mm = {
    setVaultSharedKeys(_domain, vaultKeys) {
      const value = typeof vaultKeys === "string" ? vaultKeys : JSON.stringify(vaultKeys);
      const field = document.createElement("textarea");
      field.setAttribute("aria-label", "Captured Find Hub vaultKeys");
      field.style.cssText =
        "position:fixed;top:0;left:0;width:100%;height:200px;z-index:2147483647;" +
        "background:white;color:black;font:12px monospace;padding:10px;box-sizing:border-box";
      field.value = value;
      document.body.prepend(field);
      field.focus();
      field.select();
      console.log("Find Hub key result captured locally in the text box.");
    },
    closeView() {
      console.log("Google unlock page closed without returning a key.");
    }
  };
  console.log("Local-only Find Hub unlock listener ready.");
})();
