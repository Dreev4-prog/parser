chrome.storage.local.get(["dtVintedStatus", "dtVintedPair"]).then(data => {
  const pair = data.dtVintedPair;
  const status = data.dtVintedStatus || (pair ? "Ожидаю вход в Vinted…" : "Готов. Открой одноразовую ссылку из DT Vinted Session.");
  document.getElementById("status").textContent = status;
});
