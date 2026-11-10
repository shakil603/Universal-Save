// Java HTTP service that exposes NewPipe Extractor data to the Python app.
// NewPipe Extractor source is vendored under vendor/ (GPL-3.0-or-later).

plugins {
    java
    application
    id("com.google.protobuf") version "0.10.1"
}

repositories {
    mavenCentral()
    maven(url = "https://jitpack.io")
}

java {
    toolchain {
        languageVersion.set(JavaLanguageVersion.of(17))
    }
}

sourceSets {
    main {
        java.srcDirs("vendor/extractor/src/main/java", "vendor/timeago-parser/src/main/java", "src/main/java")
        proto.srcDir("vendor/extractor/src/main/proto")
    }
}

dependencies {
    implementation("com.github.TeamNewPipe:nanojson:e9d656ddb49a412a5a0a5d5ef20ca7ef09549996")
    implementation("org.jsoup:jsoup:1.24.1")
    implementation("com.google.code.findbugs:jsr305:3.0.2")
    implementation("com.google.protobuf:protobuf-javalite:4.36.2")
    implementation("org.mozilla:rhino:1.8.1")
    implementation("org.mozilla:rhino-engine:1.8.1")
}

protobuf {
    protoc {
        artifact = "com.google.protobuf:protoc:4.36.2"
    }
    generateProtoTasks {
        all().forEach { task ->
            task.builtins {
                named("java") {
                    option("lite")
                }
            }
        }
    }
}

application {
    mainClass.set("app.NewPipeService")
    applicationDefaultJvmArgs = listOf("-Xmx192m", "-Xss512k")
}
