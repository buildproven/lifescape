const route = document.querySelector(".route-line");

if (route && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
  const length = route.getTotalLength();
  route.style.setProperty("--route-length", `${length}`);
  route.classList.add("is-drawn");
}
