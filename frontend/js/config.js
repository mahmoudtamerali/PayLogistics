/**
 * SecureCare Health — Runtime Environment Configuration
 * Same-origin `/api/*` routing on outship.me; Cognito Hosted UI PKCE settings.
 */
export const CONFIG = Object.freeze({
  brandName: "SecureCare Health",
  apiBaseUrl: "",
  cognitoDomain: "https://eu-west-1s51fcyw5c.auth.eu-west-1.amazoncognito.com",
  clientId: "265sf66rn74fpel2s798v26nd4",
  scopes: "email openid phone",
  sessionWarningSeconds: 300,
  get redirectUri() {
    return `${window.location.origin}/portal.html`;
  },
  get logoutUri() {
    return `${window.location.origin}/index.html`;
  }
});
